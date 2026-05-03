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
from shopify_tools import get_products_context
from whatsapp_client import VERIFY_TOKEN, normalize_phone, send_text

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

# Extra instruction injected into system prompt for WhatsApp replies
_WA_SYSTEM_SUFFIX = (
    "\n\nEstás respondiendo por WhatsApp. "
    "Respuestas cortas (máx 2-3 líneas). "
    "Sin markdown. "
    "Links simples."
)

router = APIRouter()


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

@router.post("/webhook/whatsapp")
async def whatsapp_incoming(request: Request):
    """
    Receive and process incoming WhatsApp messages from Meta.
    Queries Claude (Haiku) with conversation history and sends a reply.
    Non-text messages (images, audio, etc.) are silently ignored.
    """
    try:
        body = await request.json()
    except Exception:
        # Meta sometimes sends malformed bodies during retries; swallow gracefully
        return {"status": "ok"}

    try:
        value = body["entry"][0]["changes"][0]["value"]
    except (KeyError, IndexError):
        return {"status": "ok"}

    # Ignore status updates and delivery receipts
    if "messages" not in value:
        return {"status": "ok"}

    message = value["messages"][0]

    # Only handle plain-text messages
    if message.get("type") != "text":
        print(f"[whatsapp_routes] Ignoring non-text message type: {message.get('type')}")
        return {"status": "ok"}

    from_phone: str = message["from"]
    user_text: str = message["text"]["body"]

    print(f"[whatsapp_routes] Incoming message from {from_phone}: {user_text[:80]!r}")

    # ── Load conversation history ─────────────────────────────────────────────
    db = get_db()
    history: list = []
    try:
        row = db.execute(
            "SELECT history FROM whatsapp_conversations WHERE phone=?",
            (from_phone,),
        ).fetchone()
        if row:
            history = json.loads(row["history"] or "[]")
    except Exception as exc:
        print(f"[whatsapp_routes] WARNING: could not load history for {from_phone}: {exc}")

    # Keep only the last 12 messages to stay within token limits
    history = history[-12:]

    # ── Build messages list for Claude ───────────────────────────────────────
    messages = history + [{"role": "user", "content": user_text}]

    # ── Build system prompt ───────────────────────────────────────────────────
    try:
        products_ctx = get_products_context()
    except Exception as exc:
        print(f"[whatsapp_routes] WARNING: could not fetch products: {exc}")
        products_ctx = "[Catálogo no disponible temporalmente]"

    system = SYSTEM_PROMPT.replace("{products}", products_ctx) + _WA_SYSTEM_SUFFIX

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
        print(f"[whatsapp_routes] ERROR calling Claude for {from_phone}: {exc}")
        reply = "Hola, en este momento tenemos un problema técnico. Por favor contáctanos en un momento 🙏"

    # ── Persist updated history ───────────────────────────────────────────────
    updated_history = messages + [{"role": "assistant", "content": reply}]
    try:
        db.execute(
            """
            INSERT INTO whatsapp_conversations (phone, history, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(phone) DO UPDATE SET
                history    = excluded.history,
                updated_at = excluded.updated_at
            """,
            (from_phone, json.dumps(updated_history), time.time()),
        )
        db.commit()
    except Exception as exc:
        print(f"[whatsapp_routes] WARNING: could not save history for {from_phone}: {exc}")
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
            send_text(from_phone, escalate_text)
            print(f"[whatsapp_routes] Escalation sent to {from_phone}.")
            return {"status": "ok"}
    except (json.JSONDecodeError, TypeError, KeyError):
        pass  # Normal text reply — proceed below

    # ── Send reply ────────────────────────────────────────────────────────────
    try:
        send_text(from_phone, reply)
    except Exception as exc:
        print(f"[whatsapp_routes] ERROR sending message to {from_phone}: {exc}")

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
            INSERT OR IGNORE INTO abandoned_carts
                (token, phone, name, products, checkout_url, total, created_at, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'pending')
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
