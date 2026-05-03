import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

STORE_URL = os.getenv("SHOPIFY_STORE_URL")
ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
API_VERSION = "2025-01"
CACHE_TTL = 1800  # 30 minutos

_cache: dict = {"data": None, "ts": 0}


def _headers() -> dict:
    return {
        "X-Shopify-Access-Token": ADMIN_TOKEN,
        "Content-Type": "application/json",
    }


def get_products_context() -> str:
    """Devuelve catálogo formateado con precios y stock. Cache de 30 min."""
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < CACHE_TTL:
        return _cache["data"]

    try:
        url = f"https://{STORE_URL}/admin/api/{API_VERSION}/products.json"
        params = {
            "limit": 250,
            "status": "active",
            "fields": "id,title,variants,status",
        }
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        products = resp.json().get("products", [])

        lines = []
        for p in products:
            for v in p.get("variants", []):
                stock = v.get("inventory_quantity", 0)
                price = float(v.get("price", 0))
                variant_title = v.get("title", "")
                name = (
                    p["title"]
                    if variant_title == "Default Title"
                    else f"{p['title']} - {variant_title}"
                )
                disponibilidad = f"disponible ({stock} uds)" if stock > 0 else "AGOTADO"
                lines.append(f"- {name}: ${price:,.0f} CLP | {disponibilidad}")

        ctx = "\n".join(lines) if lines else "Catálogo no disponible temporalmente."
        _cache["data"] = ctx
        _cache["ts"] = now
        return ctx

    except Exception as e:
        return f"[No se pudo cargar el catálogo: {e}]"
