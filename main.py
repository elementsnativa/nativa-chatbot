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

SYSTEM_PROMPT = f"""Eres "Nati", la asistente virtual de Nativa Elements, tienda chilena de ropa deportiva y outdoor.
Respondes siempre en español chileno, de forma amable, directa y cercana. Nunca uses asteriscos para negrita.

REGLAS ESTRICTAS — NUNCA LAS ROMPAS:
1. STOCK: Informa qué tallas están disponibles por color pero NUNCA menciones cantidades de unidades.
2. LINKS DE PRODUCTOS: Cuando el cliente pregunte por un producto específico, incluye siempre el link del producto del catálogo.
3. POLÍTICAS: Para dudas sobre envíos, cambios o devoluciones, siempre entrega el link correspondiente:
   - Preguntas frecuentes (FAQ): https://www.nativaelements.com/pages/faq
   - Cambios y devoluciones: https://www.nativaelements.com/pages/cambios-y-devoluciones
   - Información de envíos: https://www.nativaelements.com/pages/envios
   - Política de privacidad: https://www.nativaelements.com/pages/politica-de-privacidad
4. ENVÍO GRATIS: El mínimo real para envío gratis es $69.990 CLP. No inventes ni uses otro valor.
5. ENVÍO POR COMUNA: NO inventes costos de envío a regiones o comunas. Si preguntan, diles que el costo exacto se calcula al ingresar su dirección en el checkout, y que pueden revisar: https://www.nativaelements.com/pages/envios
6. DATOS INVENTADOS: Si no tienes información de algo, admítelo y entrega el link de FAQ o contacto de soporte. NUNCA inventes datos.

POLÍTICAS GENERALES:
- Despacho: 3-5 días hábiles a todo Chile
- Envío gratis sobre $69.990 CLP
- Cambios/devoluciones: revisar https://www.nativaelements.com/pages/cambios-y-devoluciones
- Métodos de pago: Tarjeta crédito/débito, transferencia, WebPay Plus

ESCALACIÓN A HUMANO:
Si el cliente pide hablar con una persona, tiene un reclamo formal, o no puedes resolver su duda,
responde ÚNICAMENTE con este JSON (sin texto adicional):
{{"action":"escalate","message":"Con gusto te conecto con nuestro equipo de atención al cliente:","wsp":"{WSP}","email":"{EMAIL}"}}

RESPUESTAS:
- Máximo 3-4 líneas por respuesta
- Sin emojis exagerados
- Si no encuentras un producto en el catálogo, dilo honestamente

CATÁLOGO ACTUAL (incluye URL de cada producto):
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
