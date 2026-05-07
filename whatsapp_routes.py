"""
whatsapp_routes.py — FastAPI router for WhatsApp + Shopify webhook endpoints.

Routes:
  GET  /webhook/whatsapp              — Meta webhook verification handshake
  POST /webhook/whatsapp              — Incoming WhatsApp messages
  POST /webhook/shopify/checkout      — Shopify abandoned checkout webhook
  POST /webhook/shopify/order_paid    — Shopify order paid webhook
  GET  /setup/shopify-webhooks        — Register Shopify webhooks programmatically

Mount in main.py with:
    from whatsapp_routes import router as whatsapp_router
    app.include_router(whatsapp_router)
"""

import asyncio
import hashlib
import hmac
import json
import os
import time

import anthropic
import requests
from dotenv import load_dotenv
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import PlainTextResponse

from database import get_db
from prompts import SYSTEM_PROMPT
from shopify_tools import get_product_image, get_products_context
from whatsapp_client import VERIFY_TOKEN, normalize_phone, send_image, send_text

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

_anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

SHOPIFY_STORE_URL = os.getenv("SHOPIFY_STORE_URL")
SHOPIFY_ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
SHOPIFY_WEBHOOK_SECRET = os.getenv("SHOPIFY_WEBHOOK_SECRET")  # optional HMAC verification
RAILWAY_PUBLIC_URL = os.getenv("RAILWAY_PUBLIC_URL", "")      # e.g. https://your-app.up.railway.app
SHOPIFY_API_VERSION = "2025-01"

WSP_CONTACT = os.getenv("CONTACTO_WSP", "56912345678")
EMAIL_CONTACT = os.getenv("CONTACTO_EMAIL", "elements.nativa@gmail.com")

HUMAN_TAKEOVER_TTL = 48 * 3600  # 48 hours
CONVERSATION_TTL   = 48 * 3600  # reset history after 48h of inactivity
DEBOUNCE_SECONDS = 10  # wait this long for more messages before replying

_message_buffer: dict[str, list[str]] = {}
_pending_tasks: dict[str, asyncio.Task] = {}

# Extra instruction injected into system prompt for WhatsApp replies
_WA_SYSTEM_SUFFIX = (
    "\n\nEstás respondiendo por WhatsApp. "
    "Respuestas cortas (máx 2-3 líneas). "
    "Sin markdown. "
    "Links simples."
)

router = APIRouter()


def _set_whatsapp_takeover(phone: str) -> None:
    """Mark a WhatsApp conversation as human-managed for the next 48h."""
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO whatsapp_conversations (phone, history, updated_at, human_takeover)
            VALUES (?, '[]', ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                human_takeover = excluded.human_takeover,
                updated_at     = excluded.updated_at
            """,
            (phone, time.time(), time.time()),
        )
        db.commit()
    except Exception as exc:
        print(f"[whatsapp_routes] WARNING: could not set human_takeover for {phone}: {exc}")
    finally:
        db.close()


# ── 1. GET /webhook/whatsapp — Meta verification handshake ───────────────────

@router.get("/webhook/whatsapp", response_class=PlainTextResponse)
async def whatsapp_verify(
    mode: str = Query(None, alias="hub.mode"),
    verify_token: str = Query(None, alias="hub.verify_token"),
    challenge: str = Query(None, alias="hub.challenge"),
):
    """
    Meta calls this endpoint to verify ownership of the webhook URL.
    Responds with the challenge string on success, 403 otherwise.
    """
    print(f"[whatsapp_routes] Verification request — mode={mode}, token_match={verify_token == VERIFY_TOKEN}")

    if mode == "subscribe" and verify_token == VERIFY_TOKEN:
        print("[whatsapp_routes] Webhook verified successfully.")
        return challenge

    raise HTTPException(status_code=403, detail="Verification token mismatch")


# ── 2. POST /webhook/whatsapp — Incoming WhatsApp messages ───────────────────

async def _debounced_reply_wa(phone: str) -> None:
    """Wait DEBOUNCE_SECONDS, then process all buffered messages for this phone."""
    await asyncio.sleep(DEBOUNCE_SECONDS)

    buffered = _message_buffer.pop(phone, [])
    _pending_tasks.pop(phone, None)

    if not buffered:
        return

    user_text = "\n".join(buffered)
    print(f"[whatsapp_routes] Processing {len(buffered)} buffered message(s) from {phone}.")

    db = get_db()
    history: list = []
    human_takeover: float | None = None
    try:
        row = db.execute(
            "SELECT history, human_takeover, updated_at FROM whatsapp_conversations WHERE phone=?",
            (phone,),
        ).fetchone()
        if row:
            human_takeover = row["human_takeover"]
            if row["updated_at"] and (time.time() - row["updated_at"]) > CONVERSATION_TTL:
                history = []
                print(f"[whatsapp_routes] History expired for {phone} — starting fresh.")
            else:
                history = json.loads(row["history"] or "[]")
    except Exception as exc:
        print(f"[whatsapp_routes] WARNING: could not load history for {phone}: {exc}")

    if human_takeover and (time.time() - human_takeover) < HUMAN_TAKEOVER_TTL:
        print(f"[whatsapp_routes] Human takeover active for {phone} — bot skipped.")
        db.close()
        return

    history = history[-20:]
    messages = history + [{"role": "user", "content": user_text}]

    try:
        products_ctx = get_products_context()
    except Exception as exc:
        print(f"[whatsapp_routes] WARNING: could not fetch products: {exc}")
        products_ctx = "[Catálogo no disponible temporalmente]"

    system = SYSTEM_PROMPT.replace("{products}", products_ctx) + _WA_SYSTEM_SUFFIX

    try:
        response = _anthropic.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=system,
            messages=messages,
        )
        reply: str = response.content[0].text.strip()
    except Exception as exc:
        print(f"[whatsapp_routes] ERROR calling Claude for {phone}: {exc}")
        reply = "Hola, en este momento tenemos un problema técnico. Por favor contáctanos en un momento 🙏"

    updated_history = messages + [{"role": "assistant", "content": reply}]
    try:
        db.execute(
            """
            INSERT INTO whatsapp_conversations (phone, history, updated_at, human_takeover)
            VALUES (?, ?, ?, NULL)
            ON CONFLICT(phone) DO UPDATE SET
                history    = excluded.history,
                updated_at = excluded.updated_at
            """,
            (phone, json.dumps(updated_history), time.time()),
        )
        db.commit()
    except Exception as exc:
        print(f"[whatsapp_routes] WARNING: could not save history for {phone}: {exc}")
    finally:
        db.close()

    try:
        parsed = json.loads(reply)
        if parsed.get("action") == "escalate":
            escalate_text = (
                f"{parsed.get('message', 'Para hablar con una persona de nuestro equipo, escríbenos a:')}\n"
                f"Email: {EMAIL_CONTACT}"
            )
            send_text(phone, escalate_text)
            _set_whatsapp_takeover(phone)
            print(f"[whatsapp_routes] Escalation sent + human takeover set for {phone}.")
            return
    except (json.JSONDecodeError, TypeError, KeyError):
        pass

    import re as _re
    handles_found = _re.findall(r'nativaelements\.com/products/([\w%-]+)', reply)
    seen_handles: set = set()
    for handle in handles_found[:2]:
        if handle not in seen_handles:
            img_url = get_product_image(handle)
            if img_url:
                try:
                    send_image(phone, img_url)
                    print(f"[whatsapp_routes] Product image sent for handle '{handle}' to {phone}.")
                except Exception as img_exc:
                    print(f"[whatsapp_routes] WARNING: could not send image for '{handle}': {img_exc}")
            seen_handles.add(handle)

    try:
        send_text(phone, reply)
    except Exception as exc:
        print(f"[whatsapp_routes] ERROR sending message to {phone}: {exc}")


@router.post("/webhook/whatsapp")
async def whatsapp_incoming(request: Request):
    """
    Receive incoming WhatsApp messages from Meta.
    Buffers messages per contact and replies once after DEBOUNCE_SECONDS of silence.
    """
    try:
        body = await request.json()
    except Exception:
        return {"status": "ok"}

    try:
        value = body["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError):
        return {"status": "ok"}

    if "messages" not in value:
        return {"status": "ok"}

    message = value["messages"][0]

    if message.get("type") != "text":
        print(f"[whatsapp_routes] Ignoring non-text message type: {message.get('type')}")
        return {"status": "ok"}

    from_phone: str = message["from"]
    user_text: str = message["text"]["body"]

    print(f"[whatsapp_routes] Buffering message from {from_phone}: {user_text[:80]!r}")

    _message_buffer.setdefault(from_phone, []).append(user_text)

    task = _pending_tasks.get(from_phone)
    if task and not task.done():
        task.cancel()

    _pending_tasks[from_phone] = asyncio.create_task(_debounced_reply_wa(from_phone))

    return {"status": "ok"}


# ── 3. POST /webhook/shopify/checkout — Abandoned checkout ───────────────────

def _verify_shopify_hmac(secret: str, body_bytes: bytes, signature: str) -> bool:
    """Return True if the HMAC-SHA256 of body matches the Shopify signature header."""
    digest = hmac.new(
        secret.encode("utf-8"),
        body_bytes,
        hashlib.sha256,
    ).digest()
    import base64
    computed = base64.b64encode(digest).decode()
    return hmac.compare_digest(computed, signature)


@router.post("/webhook/shopify/checkout")
async def shopify_checkout_webhook(request: Request):
    """
    Receive Shopify checkouts/create and checkouts/update webhooks.
    Upserts a record in abandoned_carts with status='pending'.
    """
    body_bytes = await request.body()

    # ── HMAC verification ─────────────────────────────────────────────────────
    if SHOPIFY_WEBHOOK_SECRET:
        signature = request.headers.get("X-Shopify-Hmac-Sha256", "")
        if not _verify_shopify_hmac(SHOPIFY_WEBHOOK_SECRET, body_bytes, signature):
            print("[whatsapp_routes] Shopify checkout HMAC verification FAILED.")
            raise HTTPException(status_code=401, detail="Invalid HMAC signature")
    else:
        print("[whatsapp_routes] WARNING: SHOPIFY_WEBHOOK_SECRET not set — skipping HMAC verification.")

    try:
        checkout = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    # ── Extract fields ────────────────────────────────────────────────────────
    token: str = checkout.get("token") or checkout.get("id", "")
    if not token:
        print("[whatsapp_routes] Checkout webhook missing token/id — ignoring.")
        return {"status": "ok"}

    # Phone: try checkout.phone → shipping_address.phone → billing_address.phone
    raw_phone = (
        checkout.get("phone")
        or (checkout.get("shipping_address") or {}).get("phone")
        or (checkout.get("billing_address") or {}).get("phone")
    )
    phone = normalize_phone(str(raw_phone)) if raw_phone else None

    # Name: shipping_address.first_name → customer.first_name
    name = (
        (checkout.get("shipping_address") or {}).get("first_name")
        or (checkout.get("customer") or {}).get("first_name")
        or ""
    )

    # Products: list of {title, price}
    line_items = checkout.get("line_items", [])
    products = [
        {
            "title": item.get("title", "Producto"),
            "price": item.get("price", "0"),
        }
        for item in line_items
    ]

    total_price: str = str(checkout.get("total_price", "0"))
    checkout_url: str = checkout.get("abandoned_checkout_url", "")
    created_at: float = time.time()

    # ── Persist to DB (ignore if token already exists) ────────────────────────
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO abandoned_carts
                (token, phone, name, products, checkout_url, total, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
            ON CONFLICT (token) DO NOTHING
            """,
            (
                str(token),
                phone,
                name,
                json.dumps(products),
                checkout_url,
                total_price,
                created_at,
            ),
        )
        db.commit()
        print(
            f"[whatsapp_routes] Checkout {token} saved — phone={phone}, "
            f"name={name!r}, items={len(products)}, total={total_price}"
        )
    except Exception as exc:
        print(f"[whatsapp_routes] ERROR saving checkout {token}: {exc}")
    finally:
        db.close()

    return {"status": "ok"}


# ── 4. POST /webhook/shopify/order_paid ──────────────────────────────────────

@router.post("/webhook/shopify/order_paid")
async def shopify_order_paid(request: Request):
    """
    Receive Shopify orders/paid webhook.
    Records the completed order and converts any pending abandoned carts
    for the same phone number.
    """
    body_bytes = await request.body()

    # Optional HMAC verification (reuse same secret — Shopify uses one secret per webhook)
    if SHOPIFY_WEBHOOK_SECRET:
        signature = request.headers.get("X-Shopify-Hmac-Sha256", "")
        if not _verify_shopify_hmac(SHOPIFY_WEBHOOK_SECRET, body_bytes, signature):
            print("[whatsapp_routes] Order paid HMAC verification FAILED.")
            raise HTTPException(status_code=401, detail="Invalid HMAC signature")

    try:
        order = json.loads(body_bytes)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON body")

    raw_phone = (
        order.get("phone")
        or (order.get("shipping_address") or {}).get("phone")
        or (order.get("billing_address") or {}).get("phone")
    )
    phone = normalize_phone(str(raw_phone)) if raw_phone else None
    email: str = (
        order.get("email")
        or (order.get("customer") or {}).get("email")
        or ""
    )
    completed_at: float = time.time()

    db = get_db()
    try:
        # Insert completed order
        db.execute(
            "INSERT INTO completed_orders (email, phone, completed_at) VALUES (?, ?, ?)",
            (email, phone, completed_at),
        )

        # Convert any pending abandoned carts for this phone
        if phone:
            result = db.execute(
                "UPDATE abandoned_carts SET status='converted' WHERE phone=? AND status='pending'",
                (phone,),
            )
            if result.rowcount:
                print(f"[whatsapp_routes] Marked {result.rowcount} pending cart(s) as converted for phone {phone}.")

        db.commit()
        print(f"[whatsapp_routes] Order paid recorded — email={email!r}, phone={phone}.")
    except Exception as exc:
        print(f"[whatsapp_routes] ERROR recording order paid: {exc}")
    finally:
        db.close()

    return {"status": "ok"}


# ── 5. GET /setup/shopify-webhooks ────────────────────────────────────────────

@router.get("/setup/shopify-webhooks")
async def setup_shopify_webhooks():
    """
    Programmatically register the three Shopify webhooks needed by this service.
    Uses RAILWAY_PUBLIC_URL as the base URL.
    Returns a list of {topic, address, status} dicts.
    """
    if not SHOPIFY_STORE_URL or not SHOPIFY_ADMIN_TOKEN:
        raise HTTPException(status_code=500, detail="SHOPIFY_STORE_URL or SHOPIFY_ADMIN_TOKEN not set")

    if not RAILWAY_PUBLIC_URL:
        raise HTTPException(status_code=500, detail="RAILWAY_PUBLIC_URL env var is required")

    base = RAILWAY_PUBLIC_URL.rstrip("/")

    webhooks_to_register = [
        {"topic": "checkouts/create",  "address": f"{base}/webhook/shopify/checkout"},
        {"topic": "checkouts/update",  "address": f"{base}/webhook/shopify/checkout"},
        {"topic": "orders/paid",       "address": f"{base}/webhook/shopify/order_paid"},
    ]

    api_url = f"https://{SHOPIFY_STORE_URL}/admin/api/{SHOPIFY_API_VERSION}/webhooks.json"
    headers = {
        "X-Shopify-Access-Token": SHOPIFY_ADMIN_TOKEN,
        "Content-Type": "application/json",
    }

    results = []
    for wh in webhooks_to_register:
        payload = {
            "webhook": {
                "topic":   wh["topic"],
                "address": wh["address"],
                "format":  "json",
            }
        }
        try:
            resp = requests.post(api_url, json=payload, headers=headers, timeout=15)
            if resp.status_code in (200, 201):
                data = resp.json().get("webhook", {})
                results.append({
                    "topic":   wh["topic"],
                    "address": wh["address"],
                    "status":  "registered",
                    "id":      data.get("id"),
                })
                print(f"[whatsapp_routes] Webhook registered: {wh['topic']} → {wh['address']}")
            elif resp.status_code == 422:
                # Already exists — not an error
                results.append({
                    "topic":   wh["topic"],
                    "address": wh["address"],
                    "status":  "already_exists",
                })
                print(f"[whatsapp_routes] Webhook already exists: {wh['topic']}")
            else:
                results.append({
                    "topic":   wh["topic"],
                    "address": wh["address"],
                    "status":  f"error_{resp.status_code}",
                    "detail":  resp.text[:200],
                })
                print(f"[whatsapp_routes] ERROR registering {wh['topic']}: {resp.status_code} {resp.text[:200]}")
        except Exception as exc:
            results.append({
                "topic":   wh["topic"],
                "address": wh["address"],
                "status":  "exception",
                "detail":  str(exc),
            })
            print(f"[whatsapp_routes] EXCEPTION registering {wh['topic']}: {exc}")

    return {"webhooks": results}
