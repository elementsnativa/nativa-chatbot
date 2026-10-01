"""Campañas tab — how the cart recovery messages actually performed."""

import os
import re
import time
import urllib.parse

import requests
from fastapi import APIRouter

from ._shared import _auth
from database import get_db

router = APIRouter()

# An order counts as recovered only if it lands within this window of a message.
# Past it, the purchase is not credibly the campaign's doing.
ATTRIBUTION_WINDOW = 7 * 86400


def _rate(part: int, whole: int) -> float | None:
    return round(100.0 * part / whole, 1) if whole else None


@router.get("/api/dashboard/campaign-stats")
def campaign_stats(secret: str = "", days: int = 30):
    """
    Funnel per stage over the last *days*, plus conversions attributed to the
    last message a customer received before ordering.

    Click numbers are only meaningful once the templates' button points at the
    /c/ redirect, because WhatsApp never reports a tap on a URL button. Until
    then click_tracking is false and the click figures stay at zero.
    """
    _auth(secret)
    since = time.time() - days * 86400
    db = get_db()
    try:
        funnel = db.execute(
            """
            SELECT stage,
                   COUNT(*)                                        AS sent,
                   COUNT(delivered_at)                             AS delivered,
                   COUNT(read_at)                                  AS read_count,
                   COUNT(failed_at)                                AS failed,
                   COUNT(clicked_at)                               AS clicked,
                   COALESCE(SUM(click_count), 0)                   AS clicks_total,
                   COUNT(DISTINCT phone)                           AS people
            FROM   recovery_sends
            WHERE  sent_at >= ?
            GROUP  BY stage
            ORDER  BY stage
            """,
            (since,),
        ).fetchall()

        # Each order is credited to the most recent message that preceded it.
        conversions = db.execute(
            """
            SELECT s.stage,
                   COUNT(*)                                                       AS conversions,
                   AVG(o.completed_at - s.sent_at)                                AS avg_secs,
                   PERCENTILE_CONT(0.5) WITHIN GROUP (
                       ORDER BY o.completed_at - s.sent_at)                       AS median_secs
            FROM   completed_orders o
            JOIN   LATERAL (
                       SELECT rs.stage, rs.sent_at
                       FROM   recovery_sends rs
                       WHERE  rs.phone = o.phone
                         AND  rs.sent_at <= o.completed_at
                         AND  o.completed_at - rs.sent_at <= ?
                       ORDER  BY rs.sent_at DESC
                       LIMIT  1
                   ) s ON TRUE
            WHERE  o.completed_at >= ?
            GROUP  BY s.stage
            """,
            (ATTRIBUTION_WINDOW, since),
        ).fetchall()
        conv_by_stage = {r["stage"]: r for r in conversions}

        replies = db.execute(
            """
            SELECT stage, COUNT(*) AS replies, AVG(latency_seconds) AS avg_latency
            FROM   recovery_replies
            WHERE  replied_at >= ?
            GROUP  BY stage
            """,
            (since,),
        ).fetchall()
        reply_by_stage = {r["stage"]: r for r in replies}

        stages = []
        for row in funnel:
            stage = row["stage"]
            sent = row["sent"]
            conv = conv_by_stage.get(stage)
            rep = reply_by_stage.get(stage)
            stages.append({
                "stage": stage,
                "sent": sent,
                "delivered": row["delivered"],
                "read": row["read_count"],
                "failed": row["failed"],
                "clicked": row["clicked"],
                "clicks_total": row["clicks_total"],
                "replies": rep["replies"] if rep else 0,
                "conversions": conv["conversions"] if conv else 0,
                "delivered_rate": _rate(row["delivered"], sent),
                "read_rate": _rate(row["read_count"], sent),
                "click_rate": _rate(row["clicked"], row["delivered"] or sent),
                "reply_rate": _rate(rep["replies"] if rep else 0, sent),
                "conversion_rate": _rate(conv["conversions"] if conv else 0, sent),
                "hours_to_convert_avg": round(conv["avg_secs"] / 3600, 1) if conv and conv["avg_secs"] else None,
                "hours_to_convert_median": round(conv["median_secs"] / 3600, 1) if conv and conv["median_secs"] else None,
                "hours_to_reply_avg": round(rep["avg_latency"] / 3600, 1) if rep and rep["avg_latency"] else None,
            })

        totals = {
            "sent": sum(s["sent"] for s in stages),
            "delivered": sum(s["delivered"] for s in stages),
            "read": sum(s["read"] for s in stages),
            "clicked": sum(s["clicked"] for s in stages),
            "replies": sum(s["replies"] for s in stages),
            "conversions": sum(s["conversions"] for s in stages),
            "failed": sum(s["failed"] for s in stages),
        }
        totals["read_rate"] = _rate(totals["read"], totals["sent"])
        totals["click_rate"] = _rate(totals["clicked"], totals["delivered"] or totals["sent"])
        totals["conversion_rate"] = _rate(totals["conversions"], totals["sent"])

        return {
            "days": days,
            "click_tracking": bool(os.getenv("CLICK_TRACKING_DOMAIN", "").strip()),
            "stages": stages,
            "totals": totals,
        }
    finally:
        db.close()


@router.get("/api/dashboard/campaign-replies")
def campaign_replies(secret: str = "", limit: int = 50):
    """The actual text customers wrote back, newest first."""
    _auth(secret)
    db = get_db()
    try:
        rows = db.execute(
            """
            SELECT phone, stage, template, body, replied_at, latency_seconds
            FROM   recovery_replies
            ORDER  BY replied_at DESC
            LIMIT  ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
        return {"replies": [
            {
                "phone": r["phone"],
                "stage": r["stage"],
                "template": r["template"],
                "body": r["body"],
                "replied_at": r["replied_at"],
                "hours_after": round(r["latency_seconds"] / 3600, 1) if r["latency_seconds"] else None,
            }
            for r in rows
        ]}
    finally:
        db.close()


@router.get("/api/dashboard/template-health")
def template_health(secret: str = ""):
    """
    Check each cart template for the mistakes that silently break a campaign.

    A button URL carrying two placeholders is the one that cost us: the send
    succeeds, Meta reports no error, and the customer lands on a 404 — exactly
    the "messages that lead nowhere" this campaign started with.
    """
    _auth(secret)
    token = os.getenv("WHATSAPP_TOKEN", "").strip()
    if not token:
        return {"error": "Falta WHATSAPP_TOKEN", "templates": []}

    from .carritos import _resolve_waba_id
    waba_id = _resolve_waba_id(token)
    if not waba_id:
        return {"error": "No se pudo detectar el WABA ID. Agrega WHATSAPP_WABA_ID.", "templates": []}

    db = get_db()
    try:
        rows = db.execute(
            "SELECT key, value FROM bot_config WHERE key LIKE 'cart_stage%_template'"
        ).fetchall()
        wanted = {r["value"]: r["key"] for r in rows}
    finally:
        db.close()

    try:
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{waba_id}/message_templates",
            params={"access_token": token, "fields": "name,status,language,components", "limit": 100},
            timeout=12,
        )
        resp.raise_for_status()
    except requests.HTTPError as exc:
        return {"error": f"Error Meta API: {exc.response.text}", "templates": []}

    tracking_domain = os.getenv("CLICK_TRACKING_DOMAIN", "").strip().rstrip("/")
    results = []
    for t in resp.json().get("data", []):
        if t["name"] not in wanted:
            continue
        body, button_url = "", ""
        for comp in t.get("components", []):
            if comp["type"] == "BODY":
                body = comp.get("text", "")
            if comp["type"] == "BUTTONS":
                for btn in comp.get("buttons", []):
                    if btn.get("type") == "URL":
                        button_url = btn.get("url", "")

        decoded = urllib.parse.unquote(button_url)
        placeholders = re.findall(r"\{\{\d+\}\}", decoded)
        body_vars = sorted(set(re.findall(r"\{\{(\d+)\}\}", body)))

        problems = []
        if t.get("status") != "APPROVED":
            problems.append(f"No está aprobada (estado: {t.get('status')})")
        if body_vars != ["1", "2"]:
            problems.append(f"El cuerpo debe tener {{{{1}}}} y {{{{2}}}}; tiene {body_vars or 'ninguna'}")
        if not button_url:
            problems.append("No tiene botón con URL")
        elif len(placeholders) != 1:
            problems.append(
                f"La URL del botón tiene {len(placeholders)} placeholders en vez de 1 "
                f"→ el link le llega roto al cliente"
            )
        if tracking_domain and button_url and tracking_domain not in decoded:
            problems.append(
                f"CLICK_TRACKING_DOMAIN está en '{tracking_domain}' pero el botón apunta a otro dominio "
                f"→ no se van a medir clics"
            )

        results.append({
            "name": t["name"],
            "config_key": wanted[t["name"]],
            "status": t.get("status"),
            "language": t.get("language"),
            "button_url": decoded,
            "body_vars": body_vars,
            "ok": not problems,
            "problems": problems,
        })

    for name, key in wanted.items():
        if not any(r["name"] == name for r in results):
            results.append({
                "name": name, "config_key": key, "status": "NO EXISTE", "language": "",
                "button_url": "", "body_vars": [], "ok": False,
                "problems": [f"La plantilla '{name}' configurada en {key} no existe en tu WABA"],
            })

    results.sort(key=lambda r: r["config_key"])
    return {"templates": results, "click_tracking_domain": tracking_domain}
