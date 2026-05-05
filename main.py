import json
import os
from contextlib import asynccontextmanager
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import anthropic
from shopify_tools import get_products_context
from cart_recovery import start_recovery_scheduler
from whatsapp_routes import router as whatsapp_router, _set_whatsapp_takeover
from instagram_routes import router as instagram_router
from prompts import SYSTEM_PROMPT
from database import get_db

load_dotenv()


@asynccontextmanager
async def lifespan(app):
    start_recovery_scheduler()
    yield


app = FastAPI(lifespan=lifespan)
app.include_router(whatsapp_router)
app.include_router(instagram_router)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
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

    messages = req.history[-12:] + [{"role": "user", "content": req.message + page_ctx}]

    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=600,
        system=system,
        messages=messages,
    )

    reply = response.content[0].text.strip()

    try:
        parsed = json.loads(reply)
        if parsed.get("action") == "escalate":
            return {
                "reply": parsed["message"],
                "action": "escalate",
                "wsp": parsed["wsp"],
                "email": parsed["email"],
            }
    except (json.JSONDecodeError, KeyError):
        pass

    return {"reply": reply}
