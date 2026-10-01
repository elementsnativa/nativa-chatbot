import json
import os
import time
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic
from shopify_tools import get_products_context
from order_lookup import create_with_order_tool
from cart_recovery import start_recovery_scheduler
from instagram_client import start_token_refresh_scheduler
from whatsapp_routes import router as whatsapp_router, _set_whatsapp_takeover
from instagram_routes import router as instagram_router
from dashboard import router as dashboard_router
from crm import router as crm_router, start_crm_scheduler
from prompts import SYSTEM_PROMPT, WHATSAPP_CONTACT
from database import get_db

load_dotenv()


@asynccontextmanager
async def lifespan(app):
    start_recovery_scheduler()
    start_token_refresh_scheduler()
    start_crm_scheduler()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(whatsapp_router)
app.include_router(instagram_router)
app.include_router(dashboard_router)
app.include_router(crm_router)

# Only the storefront may call /chat — every request runs on our Anthropic credit.
ALLOWED_ORIGINS = [
    origin.strip()
    for origin in os.getenv(
        "ALLOWED_ORIGINS",
        "https://www.nativaelements.com,https://nativaelements.com",
    ).split(",")
    if origin.strip()
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=ALLOWED_ORIGINS,
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


class ChatRequest(BaseModel):
    message: str
    history: list = []
    page_type: Optional[str] = None   # "product", "cart", "collection", "general"
    product_name: Optional[str] = None  # nombre del producto si está en página de producto


@app.get("/health")
def health():
    return {"status": "ok", "service": "nativa-chatbot"}


ADMIN_SECRET = os.getenv("ADMIN_SECRET", "nativa-admin-2024")

# Minimum seconds between two manual test sends to the same phone.
CART_TEST_COOLDOWN = float(os.getenv("CART_TEST_COOLDOWN_SECONDS", "180"))


@app.get("/admin")
def admin_panel(secret: str = ""):
    from fastapi.responses import HTMLResponse
    if secret != ADMIN_SECRET:
        return HTMLResponse("<h3>Acceso denegado</h3>", status_code=403)
    html = f"""<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Nativa — Control del Bot</title>
  <style>
    body {{ font-family: sans-serif; max-width: 480px; margin: 40px auto; padding: 0 20px; }}
    h2 {{ color: #2d6a4f; }}
    input, select {{ width: 100%; padding: 12px; font-size: 16px; margin: 8px 0 16px; border: 1px solid #ccc; border-radius: 8px; box-sizing: border-box; }}
    button {{ width: 100%; padding: 14px; font-size: 16px; border: none; border-radius: 8px; cursor: pointer; margin-bottom: 10px; }}
    .pause {{ background: #e63946; color: white; }}
    .resume {{ background: #2d6a4f; color: white; }}
    .msg {{ padding: 12px; border-radius: 8px; margin-top: 16px; display: none; }}
    .ok {{ background: #d4edda; color: #155724; }}
    .err {{ background: #f8d7da; color: #721c24; }}
  </style>
</head>
<body>
  <h2>🤖 Control del Bot</h2>
  <p>Pausa el bot antes de escribirle a un cliente desde WhatsApp.</p>

  <label>Canal</label>
  <select id="channel">
    <option value="whatsapp">WhatsApp</option>
    <option value="instagram">Instagram</option>
  </select>

  <label>Número / ID</label>
  <input id="contact" type="text" placeholder="56912345678" inputmode="numeric">

  <button class="pause" onclick="action('pause')">⏸ Pausar bot (48h)</button>
  <button class="resume" onclick="action('resume')">▶ Reanudar bot</button>

  <div id="msg" class="msg"></div>

  <script>
    async function action(type) {{
      const ch = document.getElementById('channel').value;
      const ct = document.getElementById('contact').value.trim();
      if (!ct) {{ showMsg('Ingresa el número o ID', false); return; }}
      const url = `/admin/${{type}}/${{ch}}/${{ct}}?secret={ADMIN_SECRET}`;
      const r = await fetch(url);
      const d = await r.json();
      showMsg(r.ok ? (type === 'pause' ? '✅ Bot pausado 48h para ' + ct : '✅ Bot reanudado para ' + ct) : '❌ Error: ' + JSON.stringify(d), r.ok);
    }}
    function showMsg(text, ok) {{
      const el = document.getElementById('msg');
      el.textContent = text;
      el.className = 'msg ' + (ok ? 'ok' : 'err');
      el.style.display = 'block';
    }}
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


@app.get("/admin/pause/{channel}/{contact_id}")
def admin_pause(channel: str, contact_id: str, secret: str = ""):
    """Pause the bot for a WhatsApp phone or Instagram PSID for 48h."""
    if secret != ADMIN_SECRET:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid secret")
    import time
    db = get_db()
    if channel == "whatsapp":
        _set_whatsapp_takeover(contact_id)
    elif channel == "instagram":
        db.execute(
            """
            INSERT INTO instagram_conversations (psid, history, updated_at, human_takeover)
            VALUES (?, '[]', ?, ?)
            ON CONFLICT(psid) DO UPDATE SET
                human_takeover = excluded.human_takeover,
                updated_at     = excluded.updated_at
            """,
            (contact_id, time.time(), time.time()),
        )
        db.commit()
        db.close()
    else:
        return {"error": "channel must be 'whatsapp' or 'instagram'"}
    return {"status": "paused", "channel": channel, "contact": contact_id, "hours": 48}


@app.get("/admin/resume/{channel}/{contact_id}")
def admin_resume(channel: str, contact_id: str, secret: str = ""):
    """Resume the bot immediately for a WhatsApp phone or Instagram PSID."""
    if secret != ADMIN_SECRET:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid secret")
    db = get_db()
    table = "whatsapp_conversations" if channel == "whatsapp" else "instagram_conversations"
    col = "phone" if channel == "whatsapp" else "psid"
    db.execute(f"UPDATE {table} SET human_takeover = NULL WHERE {col} = ?", (contact_id,))
    db.commit()
    db.close()
    return {"status": "resumed", "channel": channel, "contact": contact_id}


@app.get("/c/{token}")
def cart_redirect(token: str):
    """
    Click-tracking redirect for cart recovery buttons.

    WhatsApp never reports a tap on a URL button, so a click is only measurable
    if the link goes through here first. Pointing a template's button at
    https://<tracking domain>/c/{{1}} and passing the cart token makes the tap
    observable; the customer just sees a redirect to their checkout.

    Unknown tokens fall back to the cart page rather than erroring — a customer
    who taps a real message must always land somewhere useful.
    """
    import cart_tracking
    from fastapi.responses import RedirectResponse

    fallback = f"https://{os.getenv('STORE_PUBLIC_DOMAIN', 'www.nativaelements.com')}/cart"
    destination = cart_tracking.record_click(token) or fallback
    return RedirectResponse(destination, status_code=302)


@app.get("/admin/cart-test/{phone}")
def admin_cart_test(phone: str, secret: str = "", stage: int = 1,
                    name: str = "Sebastián Pérez",
                    products: str = '[{"title": "Polera Negra Oversize M"}, {"title": "Short Trail"}]',
                    dry: bool = False):
    """
    Send one real cart-recovery template to a phone, to verify end to end that
    the parameters, the language and above all the URL button work before any
    customer is messaged.

    The button points at the storefront cart rather than a real abandoned
    checkout, since a test has no checkout token to borrow.

    Example:
      /admin/cart-test/56951985753?secret=...&stage=1
      /admin/cart-test/56951985753?secret=...&stage=1&dry=1   (nothing is sent)

    dry=1 resolves the template, the language and every parameter and returns
    them without calling Meta, so a deploy can be verified without paying for
    a marketing message or messaging a real phone.
    """
    if secret != ADMIN_SECRET:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Invalid secret")

    from whatsapp_client import normalize_phone, send_template
    from cart_recovery import STAGES, first_name_of, format_products, get_config

    to = normalize_phone(phone)
    if not to:
        return {"error": f"could not normalise phone {phone!r}"}
    if not 1 <= stage <= len(STAGES):
        return {"error": f"stage must be between 1 and {len(STAGES)}"}

    _, _, config_key, fallback = STAGES[stage - 1]
    db = get_db()
    try:
        template = get_config(db, config_key, fallback)
        language = get_config(db, "cart_template_lang", "es")
    finally:
        db.close()

    body_params = [first_name_of(name), format_products(products)]

    if dry:
        return {
            "sent": False, "dry_run": True, "to": to, "template": template,
            "language": language, "body_params": body_params, "button": "cart",
            "preview": f"Hola {body_params[0]}, dejaste {body_params[1]} en tu carrito.",
            "cooldown_seconds": CART_TEST_COOLDOWN,
        }

    # A send behind a GET repeats itself: browsers prefetch links from the
    # address bar, resend on reload, and WhatsApp fetches a URL to build its
    # preview when the link is pasted into a chat. One intended test became
    # five real messages that way, so repeats inside the cooldown are refused.
    now = time.time()
    db = get_db()
    try:
        recent = db.execute(
            "SELECT sent_at FROM test_sends WHERE phone = ? AND sent_at > ? ORDER BY sent_at DESC LIMIT 1",
            (to, now - CART_TEST_COOLDOWN),
        ).fetchone()
        if recent:
            wait = int(CART_TEST_COOLDOWN - (now - float(recent["sent_at"])))
            return {
                "sent": False, "to": to, "blocked": "cooldown",
                "message": f"Ya se envió un mensaje de prueba a este número hace menos de "
                           f"{int(CART_TEST_COOLDOWN)}s. Esperá {wait}s para volver a probar, "
                           f"o usá dry=1 para verificar sin enviar.",
            }

        try:
            response = send_template(to, template, body_params,
                                     language=language, button_params=["cart"])
        except Exception as exc:
            body = getattr(getattr(exc, "response", None), "text", "")
            return {
                "sent": False, "to": to, "template": template, "language": language,
                "body_params": body_params, "error": str(exc), "meta_response": body[:600],
            }

        message_id = (response.get("messages") or [{}])[0].get("id")
        db.execute(
            "INSERT INTO test_sends (phone, stage, template, message_id, sent_at) VALUES (?, ?, ?, ?, ?)",
            (to, stage, template, message_id, now),
        )
        db.commit()
    finally:
        db.close()

    return {
        "sent": True, "to": to, "template": template, "language": language,
        "body_params": body_params,
        "button": "cart",
        "message_id": message_id,
    }


@app.get("/data-deletion")
def data_deletion():
    from fastapi.responses import HTMLResponse
    html = """
    <html><body>
    <h2>Eliminación de datos de usuario — Nativa Elements</h2>
    <p>Para solicitar la eliminación de tus datos, contáctanos:</p>
    <p>Email: <a href="mailto:elements.nativa@gmail.com">elements.nativa@gmail.com</a></p>
    <p>Eliminaremos tu información en un plazo de 30 días hábiles.</p>
    </body></html>
    """
    return HTMLResponse(content=html)


@app.post("/chat")
async def chat(req: ChatRequest):
    products_ctx = get_products_context()
    system = SYSTEM_PROMPT.replace("{products}", products_ctx)

    # Contexto de página para personalizar la respuesta
    page_ctx = ""
    if req.page_type == "product" and req.product_name:
        page_ctx = f"\n[CONTEXTO: El cliente está viendo la página del producto '{req.product_name}'. Ayúdalo a decidir su compra.]"
    elif req.page_type == "cart":
        page_ctx = "\n[CONTEXTO: El cliente está en el carrito de compras. Ayúdalo a completar su pedido y resuelve cualquier duda final.]"
    elif req.page_type == "collection":
        page_ctx = "\n[CONTEXTO: El cliente está explorando una colección de productos. Ayúdalo a encontrar lo que busca.]"

    messages = req.history[-10:] + [{"role": "user", "content": req.message + page_ctx}]

    reply = create_with_order_tool(
        _client,
        model="claude-haiku-4-5-20251001",
        max_tokens=400,
        system=system,
        messages=messages,
    )

    try:
        parsed = json.loads(reply)
        if parsed.get("action") == "escalate":
            return {
                "reply": parsed["message"],
                "action": "escalate",
                "email": parsed["email"],
                "whatsapp": WHATSAPP_CONTACT,
            }
    except (json.JSONDecodeError, KeyError):
        pass

    return {"reply": reply}
