"""
order_lookup.py — Consulta de pedidos en Shopify para el chatbot (Cyber octubre 2026).

El modelo llama a la herramienta `consultar_pedido` con el número de pedido.
Nunca se
entregan direcciones, correos, teléfonos ni montos.
"""

import json
import re
from datetime import date, datetime, timedelta

import requests

import bluex
from shopify_tools import API_VERSION, STORE_URL, _headers

CYBER_START = date(2026, 10, 2)
CYBER_END = date(2026, 10, 18)
CYBER_BUSINESS_DAYS = 14

# Feriados en Chile que caen en días hábiles dentro del plazo de despacho
FERIADOS = {
    date(2026, 10, 12),  # Encuentro de Dos Mundos
    date(2026, 12, 8),   # Inmaculada Concepción
}

TOOL = {
    "name": "consultar_pedido",
    "description": (
        "Busca un pedido de la tienda en Shopify para ver su fecha de compra, si es un "
        "pedido del Cyber, hasta qué fecha tiene plazo de despacho y si ya fue despachado. "
        "Úsala cuando el cliente te dio su número de pedido (ej: #NTVA13556)."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "numero_pedido": {"type": "string", "description": "Número de pedido, ej: NTVA1234 o 1234"},
        },
        "required": ["numero_pedido"],
    },
}


def add_business_days(start: date, days: int) -> date:
    current = start
    while days > 0:
        current += timedelta(days=1)
        if current.weekday() < 5 and current not in FERIADOS:
            days -= 1
    return current


def lookup_order(numero_pedido: str, today: date | None = None) -> dict:
    today = today or date.today()
    digits = re.sub(r"\D", "", numero_pedido or "")
    if not digits:
        return {"encontrado": False, "motivo": "numero_invalido"}

    try:
        resp = requests.get(
            f"https://{STORE_URL}/admin/api/{API_VERSION}/orders.json",
            headers=_headers(),
            params={
                "name": f"#NTVA{digits}",
                "status": "any",
                "fields": "name,created_at,cancelled_at,fulfillment_status,fulfillments,line_items",
            },
            timeout=15,
        )
        resp.raise_for_status()
        orders = resp.json().get("orders", [])
    except Exception as exc:
        print(f"[order_lookup] ERROR looking up order {digits}: {exc}")
        return {"encontrado": False, "motivo": "error_sistema"}

    order = next((o for o in orders if re.sub(r"\D", "", o.get("name", "")) == digits), None)
    if not order:
        return {"encontrado": False, "motivo": "no_existe"}

    created = datetime.fromisoformat(order["created_at"]).date()
    es_cyber = CYBER_START <= created <= CYBER_END
    result = {
        "encontrado": True,
        "pedido": order.get("name"),
        "fecha_compra": created.isoformat(),
        "hoy": today.isoformat(),
        "es_pedido_cyber": es_cyber,
        "cancelado": bool(order.get("cancelled_at")),
        "despachado": order.get("fulfillment_status") in ("fulfilled", "partial"),
    }
    titles = [(item.get("title") or "").upper() for item in order.get("line_items") or []]
    if any("COMPRESS" in t and "PREVENTA" in t for t in titles):
        result["contiene_preventa_compress"] = True
    if es_cyber:
        limite = add_business_days(created, CYBER_BUSINESS_DAYS)
        result["plazo_despacho_hasta"] = limite.isoformat()
        result["dentro_del_plazo"] = today <= limite
    fulfillments = [f for f in order.get("fulfillments") or [] if f.get("status") != "cancelled"]
    if fulfillments:
        latest = max(fulfillments, key=lambda f: f.get("created_at") or "")
        result["fecha_despacho"] = datetime.fromisoformat(latest["created_at"]).date().isoformat()
        numbers = latest.get("tracking_numbers") or []
        urls = latest.get("tracking_urls") or []
        if numbers:
            result["numero_seguimiento_bluexpress"] = numbers[0]
        if urls:
            result["link_seguimiento"] = urls[0]
        elif numbers:
            result["link_seguimiento"] = f"https://www.bluex.cl/seguimiento/?n_seguimiento={numbers[0]}"
        if numbers:
            status = bluex.get_shipment_status(numbers[0])
            if status:
                result["bluexpress"] = status
    return result


def create_with_order_tool(client, **kwargs):
    """messages.create con la herramienta de pedidos; resuelve las llamadas y devuelve el texto final."""
    messages = list(kwargs.pop("messages"))
    for _ in range(3):
        response = client.messages.create(tools=[TOOL], messages=messages, **kwargs)
        if response.stop_reason != "tool_use":
            break
        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type == "tool_use":
                data = lookup_order(block.input.get("numero_pedido", ""))
                results.append({
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": json.dumps(data, ensure_ascii=False),
                })
        messages.append({"role": "user", "content": results})
    text = "".join(b.text for b in response.content if b.type == "text").strip()
    return text or "No pude revisar tu pedido en este momento. Escríbenos al WhatsApp +56 9 7700 2403 y te ayudamos."
