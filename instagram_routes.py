"""
instagram_routes.py — FastAPI router for Instagram Messaging webhook.

Routes:
  GET  /webhook/instagram  — Meta webhook verification handshake
  POST /webhook/instagram  — Incoming Instagram DMs

Mount in main.py with:
    from instagram_routes import router as instagram_router
    app.include_router(instagram_router)
"""

import asyncio
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
BOT_RESUME_CODE = os.getenv("BOT_RESUME_CODE", "NATIVA-ON").upper()
BOT_RESUME_CONFIRM = "Bot activado ✓"

# After this many seconds without a human reply, the bot resumes automatically
HUMAN_TAKEOVER_TTL = 48 * 3600  # 48 hours
CONVERSATION_TTL   = 48 * 3600  # reset history after 48h of inactivity
DEBOUNCE_SECONDS = 10

_message_buffer: dict[str, list[str]] = {}
_pending_tasks: dict[str, asyncio.Task] = {}
_bot_sent_mids: set[str] = set()  # mids of messages sent by the bot, to ignore their echoes

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

async def _debounced_reply_ig(psid: str) -> None:
    """Wait DEBOUNCE_SECONDS, then process all buffered messages for this PSID."""
    await asyncio.sleep(DEBOUNCE_SECONDS)

    buffered = _message_buffer.pop(psid, [])
    _pending_tasks.pop(psid, None)

    if not buffered:
        return

    user_text = "\n".join(buffered)
    print(f"[instagram_routes] Processing {len(buffered)} buffered message(s) from {psid}.")

    db = get_db()
    history: list = []
    human_takeover: float | None = None
    try:
        row = db.execute(
            "SELECT history, human_takeover, updated_at FROM instagram_conversations WHERE psid=?",
            (psid,),
        ).fetchone()
        if row:
            human_takeover = row["human_takeover"]
            if row["updated_at"] and (time.time() - row["updated_at"]) > CONVERSATION_TTL:
                history = []
                print(f"[instagram_routes] History expired for {psid} — starting fresh.")
            else:
                history = json.loads(row["history"] or "[]")
    except Exception as exc:
        print(f"[instagram_routes] WARNING: could not load history for {psid}: {exc}")

    if human_takeover and (time.time() - human_takeover) < HUMAN_TAKEOVER_TTL:
        print(f"[instagram_routes] Human takeover active for {psid} — bot skipped.")
        db.close()
        return

    history = history[-10:]
    messages = history + [{"role": "user", "content": user_text}]

    try:
        products_ctx = get_products_context()
    except Exception as exc:
        print(f"[instagram_routes] WARNING: could not fetch products: {exc}")
        products_ctx = "[Catálogo no disponible temporalmente]"

    system = SYSTEM_PROMPT.replace("{products}", products_ctx) + _IG_SYSTEM_SUFFIX

    try:
        response = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=300,
            system=system,
            messages=messages,
        )
        reply: str = response.content[0].text.strip()
    except Exception as exc:
        print(f"[instagram_routes] ERROR calling Claude for {psid}: {exc}")
        reply = "Hola, en este momento tenemos un problema técnico. Por favor escríbenos en un momento 🙏"

    updated_history = messages + [{"role": "assistant", "content": reply}]
    try:
        db.execute(
            """
            INSERT INTO instagram_conversations (psid, history, updated_at, human_takeover)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(psid) DO UPDATE SET
                history    = excluded.history,
                updated_at = excluded.updated_at
            """,
            (psid, json.dumps(updated_history), time.time()),
        )
        db.commit()
    except Exception as exc:
        print(f"[instagram_routes] WARNING: could not save history for {psid}: {exc}")
    finally:
        db.close()

    try:
        parsed = json.loads(reply)
        if parsed.get("action") == "escalate":
            escalate_text = (
                f"{parsed.get('message', 'Para hablar con una persona de nuestro equipo, escríbenos a:')}\n"
                f"Email: {EMAIL_CONTACT}"
            )
            send_text(psid, escalate_text)
            print(f"[instagram_routes] Escalation sent to {psid}.")
            return
    except (json.JSONDecodeError, TypeError, KeyError):
        pass

    try:
        result = send_text(psid, reply)
        mid = result.get("message_id")
        if mid:
            _bot_sent_mids.add(mid)
            if len(_bot_sent_mids) > 500:
                _bot_sent_mids.clear()
    except Exception as exc:
        print(f"[instagram_routes] ERROR sending message to {psid}: {exc}")


@router.post("/webhook/instagram")
async def instagram_incoming(request: Request):
    """
    Receive incoming Instagram DMs from Meta.
    Buffers messages per contact and replies once after DEBOUNCE_SECONDS of silence.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    print(f"[instagram_routes] POST received — object={body.get('object')!r} — raw={str(body)[:300]}")

    if body.get("object") != "instagram":
        return {"status": "ok"}

    try:
        entry = body["entry"][0]
        messaging = entry["messaging"][0]
    except (KeyError, IndexError):
        return {"status": "ok"}

    message = messaging.get("message", {})

    # Human takeover: only when a human from the page sends a message (not the bot's own echoes)
    if message.get("is_echo") and "text" in message:
        # Ignore echoes of messages sent by the bot itself
        echo_mid = message.get("mid", "")
        if echo_mid in _bot_sent_mids or message.get("text", "").strip() == BOT_RESUME_CONFIRM:
            return {"status": "ok"}
        sender_id: str = messaging["recipient"]["id"]
        db = get_db()
        try:
            db.execute(
                """
                INSERT INTO instagram_conversations (psid, history, updated_at, human_takeover)
                VALUES (?, '[]', ?, ?)
                ON CONFLICT(psid) DO UPDATE SET
                    human_takeover = excluded.human_takeover,
                    updated_at     = excluded.updated_at
                """,
                (sender_id, time.time(), time.time()),
            )
            db.commit()
            print(f"[instagram_routes] Human takeover activated for {sender_id}.")
        except Exception as exc:
            print(f"[instagram_routes] WARNING: could not set human_takeover for {sender_id}: {exc}")
        finally:
            db.close()
        return {"status": "ok"}

    if "text" not in message:
        print(f"[instagram_routes] Ignoring non-text message: {list(message.keys())}")
        return {"status": "ok"}

    sender_id: str = messaging["sender"]["id"]
    user_text: str = message["text"]

    # Resume code: reactivate the bot for this conversation
    if user_text.strip().upper() == BOT_RESUME_CODE:
        db = get_db()
        try:
            db.execute(
                "UPDATE instagram_conversations SET human_takeover = NULL WHERE psid = ?",
                (sender_id,),
            )
            db.commit()
            print(f"[instagram_routes] Bot resumed for {sender_id} via resume code.")
        except Exception as exc:
            print(f"[instagram_routes] WARNING: could not resume bot for {sender_id}: {exc}")
        finally:
            db.close()
        try:
            send_text(sender_id, BOT_RESUME_CONFIRM)
        except Exception as exc:
            print(f"[instagram_routes] WARNING: could not send resume confirmation to {sender_id}: {exc}")
        return {"status": "ok"}

    print(f"[instagram_routes] Buffering message from {sender_id}: {user_text[:80]!r}")

    _message_buffer.setdefault(sender_id, []).append(user_text)

    task = _pending_tasks.get(sender_id)
    if task and not task.done():
        task.cancel()

    _pending_tasks[sender_id] = asyncio.create_task(_debounced_reply_ig(sender_id))

    return {"status": "ok"}
