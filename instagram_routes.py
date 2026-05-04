"""
instagram_routes.py — FastAPI router for Instagram Messaging webhook.

Routes:
  GET  /webhook/instagram  — Meta webhook verification handshake
  POST /webhook/instagram  — Incoming Instagram DMs

Mount in main.py with:
    from instagram_routes import router as instagram_router
    app.include_router(instagram_router)
"""

import json
import re
import time

import anthropic
import os
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from database import get_db
from instagram_client import VERIFY_TOKEN, send_image, send_text
from prompts import SYSTEM_PROMPT
from shopify_tools import get_product_image, get_products_context

load_dotenv()

_anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

WSP_CONTACT = os.getenv("CONTACTO_WSP", "56912345678")
EMAIL_CONTACT = os.getenv("CONTACTO_EMAIL", "elements.nativa@gmail.com")

_IG_SYSTEM_SUFFIX = (
    "\n\nEstás respondiendo por Instagram Direct. "
    "Respuestas cortas (máx 2-3 líneas). "
    "Sin markdown. "
    "Links simples."
)

router = APIRouter()


# ── 1. GET /webhook/instagram — Meta verification handshake ─────────────────

@router.get("/webhook/instagram", response_class=PlainTextResponse)
async def instagram_verify(
    mode: str = Query(None, alias="hub.mode"),
    verify_token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    print(f"[instagram_routes] Verification request — mode={mode}, token_match={verify_token == VERIFY_TOKEN}")

    if mode == "subscribe" and verify_token == VERIFY_TOKEN:
        print("[instagram_routes] Webhook verified successfully.")
        return challenge

    raise HTTPException(status_code=403, detail="Verification token mismatch")


# ── 2. POST /webhook/instagram — Incoming Instagram DMs ─────────────────────

@router.post("/webhook/instagram")
async def instagram_incoming(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    # Instagram sends object="instagram"; ignore everything else
    if body.get("object") != "instagram":
        return {"status": "ok"}

    try:
        entry = body["entry"][0]
        messaging = entry["messaging"][0]
    except (KeyError, IndexError):
        return {"status": "ok"}

    # Ignore echos (messages sent by the page itself)
    if messaging.get("message", {}).get("is_echo"):
        return {"status": "ok"}

    # Only handle plain-text messages
    message = messaging.get("message", {})
    if "text" not in message:
        print(f"[instagram_routes] Ignoring non-text message: {list(message.keys())}")
        return {"status": "ok"}

    sender_id: str = messaging["sender"]["id"]
    user_text: str = message["text"]

    print(f"[instagram_routes] Incoming DM from {sender_id}: {user_text[:80]!r}")

    # ── Load conversation history ─────────────────────────────────────────────
    db = get_db()
    history: list = []
    try:
        row = db.execute(
            "SELECT history FROM instagram_conversations WHERE psid=?",
            (sender_id,),
        ).fetchone()
        if row:
            history = json.loads(row["history"] or "[]")
    except Exception as exc:
        print(f"[instagram_routes] WARNING: could not load history for {sender_id}: {exc}")

    history = history[-12:]

    # ── Build messages list for Claude ───────────────────────────────────────
    messages = history + [{"role": "user", "content": user_text}]

    # ── Build system prompt ───────────────────────────────────────────────────
    try:
        products_ctx = get_products_context()
    except Exception as exc:
        print(f"[instagram_routes] WARNING: could not fetch products: {exc}")
        products_ctx = "[Catálogo no disponible temporalmente]"

    system = SYSTEM_PROMPT.replace("{products}", products_ctx) + _IG_SYSTEM_SUFFIX

    # ── Call Claude ───────────────────────────────────────────────────────────
    try:
        response = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=system,
            messages=messages,
        )
        reply: str = response.content[0].text.strip()
    except Exception as exc:
        print(f"[instagram_routes] ERROR calling Claude for {sender_id}: {exc}")
        reply = "Hola, en este momento tenemos un problema técnico. Por favor escríbenos en un momento 🙏"

    # ── Persist updated history ───────────────────────────────────────────────
    updated_history = messages + [{"role": "assistant", "content": reply}]
    try:
        db.execute(
            """
            INSERT INTO instagram_conversations (psid, history, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(psid) DO UPDATE SET
                history    = excluded.history,
                updated_at = excluded.updated_at
            """,
            (sender_id, json.dumps(updated_history), time.time()),
        )
        db.commit()
    except Exception as exc:
        print(f"[instagram_routes] WARNING: could not save history for {sender_id}: {exc}")
    finally:
        db.close()

    # ── Check for escalation action ───────────────────────────────────────────
    try:
        parsed = json.loads(reply)
        if parsed.get("action") == "escalate":
            escalate_text = (
                f"{parsed.get('message', 'Te conecto con nuestro equipo:')}\n"
                f"WhatsApp: +{WSP_CONTACT}\n"
                f"Email: {EMAIL_CONTACT}"
            )
            send_text(sender_id, escalate_text)
            print(f"[instagram_routes] Escalation sent to {sender_id}.")
            return {"status": "ok"}
    except (json.JSONDecodeError, TypeError, KeyError):
        pass

    # ── Send product images if reply mentions specific products ───────────────
    handles_found = re.findall(r'nativaelements\.com/products/([\w%-]+)', reply)
    seen_handles: set = set()
    for handle in handles_found[:2]:
        if handle not in seen_handles:
            img_url = get_product_image(handle)
            if img_url:
                try:
                    send_image(sender_id, img_url)
                    print(f"[instagram_routes] Product image sent for '{handle}' to {sender_id}.")
                except Exception as img_exc:
                    print(f"[instagram_routes] WARNING: could not send image for '{handle}': {img_exc}")
            seen_handles.add(handle)

    # ── Send reply ────────────────────────────────────────────────────────────
    try:
        send_text(sender_id, reply)
    except Exception as exc:
        print(f"[instagram_routes] ERROR sending message to {sender_id}: {exc}")

    return {"status": "ok"}
