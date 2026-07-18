"""Carritos tab — cart recovery stats + template config."""

import os
import time

import requests
from fastapi import APIRouter
from pydantic import BaseModel

from ._shared import _auth
from database import get_db

router = APIRouter()


def _resolve_waba_id(token: str) -> str:
    """Try to auto-detect WABA ID from the phone number ID already in env."""
    waba_id = os.getenv("WHATSAPP_WABA_ID", "").strip()
    if waba_id:
        return waba_id
    phone_id = os.getenv("WHATSAPP_PHONE_ID", "").strip()
    if not phone_id:
        return ""
    # Attempt 1: edge /whatsapp_business_account on the phone node
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{phone_id}/whatsapp_business_account",
            params={"access_token": token, "fields": "id"},
            timeout=8,
        )
        if r.ok:
            wid = r.json().get("id", "")
            if wid:
                return wid
    except Exception:
        pass
    # Attempt 2: account_id field on the phone node
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{phone_id}",
            params={"access_token": token, "fields": "id,account_id"},
            timeout=8,
        )
        if r.ok:
            wid = r.json().get("account_id", "")
            if wid:
                return wid
    except Exception:
        pass
    return ""


@router.get("/api/dashboard/phone-info")
def get_phone_info(secret: str = ""):
    _auth(secret)
    token    = os.getenv("WHATSAPP_TOKEN", "").strip()
    phone_id = os.getenv("WHATSAPP_PHONE_ID", "").strip()
    if not token or not phone_id:
        return {"error": "Faltan variables WHATSAPP_TOKEN o WHATSAPP_PHONE_ID"}
    try:
        r = requests.get(
            f"https://graph.facebook.com/v21.0/{phone_id}",
            params={"access_token": token, "fields": "id,display_phone_number,verified_name"},
            timeout=8,
        )
        r.raise_for_status()
        return r.json()
    except requests.HTTPError as exc:
        return {"error": exc.response.text}


@router.get("/api/dashboard/waba-templates")
def get_waba_templates(secret: str = ""):
    _auth(secret)
    token = os.getenv("WHATSAPP_TOKEN", "").strip()
    if not token:
        return {"error": "Falta la variable WHATSAPP_TOKEN en Railway", "templates": []}
    waba_id = _resolve_waba_id(token)
    if not waba_id:
        return {
            "error": "No se pudo detectar el WABA ID automáticamente. Agrega WHATSAPP_WABA_ID en Railway (Meta Business Suite → Configuración → Cuentas de WhatsApp → ID de cuenta).",
            "templates": [],
        }
    try:
        resp = requests.get(
            f"https://graph.facebook.com/v21.0/{waba_id}/message_templates",
            params={"access_token": token, "fields": "name,status,language,components", "limit": 100},
            timeout=10,
        )
        resp.raise_for_status()
        templates = []
        for t in resp.json().get("data", []):
            preview, has_button, button_url_example = "", False, ""
            for comp in t.get("components", []):
                if comp["type"] == "BODY":
                    preview = comp.get("text", "")
                if comp["type"] == "BUTTONS":
                    has_button = True
                    for btn in comp.get("buttons", []):
                        if btn.get("type") == "URL":
                            button_url_example = btn.get("url", "")
            templates.append({
                "name": t["name"],
                "status": t.get("status", ""),
                "language": t.get("language", ""),
                "preview": preview,
                "has_button": has_button,
                "button_url_example": button_url_example,
            })
        templates.sort(key=lambda x: (x["status"] != "APPROVED", x["name"]))
        return {"templates": templates}
    except requests.HTTPError as exc:
        return {"error": f"Error Meta API: {exc.response.text}", "templates": []}


@router.get("/api/dashboard/cart-stats")
def cart_stats(secret: str = ""):
    _auth(secret)
    db = get_db()
    try:
        rows = db.execute(
            "SELECT status, COUNT(*) as count FROM abandoned_carts GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["count"] for r in rows}
        return {
            "pending":   stats.get("pending", 0),
            "sent":      stats.get("sent", 0) + stats.get("sent_followup_pending", 0),
            "converted": stats.get("converted", 0),
            "error":     stats.get("error", 0),
            "skipped":   stats.get("skipped", 0),
            "no_phone":  stats.get("no_phone", 0),
            "total":     sum(stats.values()),
        }
    finally:
        db.close()


@router.get("/api/dashboard/cart-config")
def get_cart_config(secret: str = ""):
    _auth(secret)
    db = get_db()
    try:
        rows = db.execute(
            "SELECT key, value FROM bot_config WHERE key LIKE 'cart_template_%'"
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        db.close()


class CartConfigBody(BaseModel):
    cart_template_first: str
    cart_template_returning: str
    cart_template_followup: str


@router.put("/api/dashboard/cart-config")
def save_cart_config(body: CartConfigBody, secret: str = ""):
    _auth(secret)
    now = time.time()
    db = get_db()
    try:
        for key, value in body.model_dump().items():
            db.execute(
                "INSERT INTO bot_config (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now),
            )
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()
