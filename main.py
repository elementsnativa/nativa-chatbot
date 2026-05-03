import json
import os
from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import anthropic
from shopify_tools import get_products_context

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["POST", "GET"],
    allow_headers=["*"],
)

_client = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))

WSP = os.getenv("CONTACTO_WSP", "56912345678")
EMAIL = os.getenv("CONTACTO_EMAIL", "elements.nativa@gmail.com")

SYSTEM_PROMPT = f"""Eres "Nati", la asistente virtual de Nativa Elements, tienda chilena de equipamiento outdoor y aventura.
Respondes siempre en español chileno, de forma amable, directa y cercana. Nunca uses asteriscos para negrita.

CAPACIDADES:
- Responder sobre productos, precios y stock (catálogo actualizado al final de este mensaje)
- Explicar políticas de cambios, reembolsos y envíos
- Orientar sobre el estado de pedidos (pide el número de orden #XXXX)
- Resolver dudas frecuentes

POLÍTICAS:
- Despacho: 3-5 días hábiles a todo Chile. Gratis sobre $50.000 CLP
- Cambios/devoluciones: 30 días desde la compra, producto sin uso y con embalaje original
- Reembolsos: 5-7 días hábiles, mismo medio de pago
- Métodos de pago: Tarjeta crédito/débito, transferencia, WebPay Plus

ESCALACIÓN A HUMANO:
Si el cliente pide hablar con una persona, tiene un reclamo formal, o no puedes resolver su duda,
responde ÚNICAMENTE con este JSON (sin texto adicional):
{{"action":"escalate","message":"Con gusto te conecto con nuestro equipo de atención al cliente:","wsp":"{WSP}","email":"{EMAIL}"}}

RESPUESTAS:
- Máximo 3-4 líneas por respuesta
- Sin emojis exagerados
- Si no encuentras un producto en el catálogo, dilo honestamente

CATÁLOGO ACTUAL:
{{products}}
"""


class ChatRequest(BaseModel):
    message: str
    history: list = []


@app.get("/health")
def health():
    return {"status": "ok", "service": "nativa-chatbot"}


@app.post("/chat")
async def chat(req: ChatRequest):
    products_ctx = get_products_context()
    system = SYSTEM_PROMPT.replace("{products}", products_ctx)

    messages = req.history[-10:] + [{"role": "user", "content": req.message}]

    response = _client.messages.create(
        model="claude-haiku-4-5-20251001",
        max_tokens=512,
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
