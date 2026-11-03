import os
import re
import time
import requests
from dotenv import load_dotenv

load_dotenv()

STORE_URL = os.getenv("SHOPIFY_STORE_URL")
ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
API_VERSION = "2025-01"
STORE_DOMAIN = "nativaelements.com"
CACHE_TTL = 1800  # 30 minutos

_cache: dict = {"data": None, "ts": 0}
_image_map: dict = {}  # handle → first image URL


def _headers() -> dict:
    return {
        "X-Shopify-Access-Token": ADMIN_TOKEN,
        "Content-Type": "application/json",
    }


def get_products_context() -> str:
    """Catálogo agrupado por producto: tallas disponibles (sin cantidades) + URL."""
    now = time.time()
    if _cache["data"] and now - _cache["ts"] < CACHE_TTL:
        return _cache["data"]

    try:
        url = f"https://{STORE_URL}/admin/api/{API_VERSION}/products.json"
        params = {
            "limit": 250,
            "status": "active",
            "fields": "id,title,handle,variants,status,body_html,images",
        }
        resp = requests.get(url, headers=_headers(), params=params, timeout=15)
        resp.raise_for_status()
        products = resp.json().get("products", [])

        _image_map.clear()
        lines = []
        for p in products:
            handle = p.get("handle", "")
            images = p.get("images", [])
            if images:
                _image_map[handle] = images[0]["src"]
            product_url = f"https://{STORE_DOMAIN}/products/{handle}"
            variants = p.get("variants", [])

            # Precio base (mínimo entre variantes)
            prices = [float(v.get("price", 0)) for v in variants if v.get("price")]
            price = min(prices) if prices else 0

            # Tallas disponibles (sin mostrar cantidades)
            available = []
            total_stock = 0
            for v in variants:
                stock = v.get("inventory_quantity", 0)
                total_stock += stock
                vt = v.get("title", "")
                if stock > 0 and vt and vt != "Default Title":
                    available.append(vt)

            if available:
                tallas = f"Tallas disponibles: {', '.join(available)}"
            elif total_stock > 0:
                tallas = "disponible"
            else:
                tallas = "AGOTADO"

            # Del body_html solo conservamos la referencia de talla. Antes se
            # mandaban 200 chars de descripción por producto y en muchos casos
            # era CSS de los bloques <style>: ~8.000 tokens por mensaje que el
            # bot pagaba sin usar. Lo demás ya está en conocimiento.txt.
            desc_part = _size_reference(p.get("body_html") or "")

            lines.append(
                f"- {p['title']}: ${price:,.0f} CLP | {tallas}{desc_part} | {product_url}"
            )

        ctx = "\n".join(lines) if lines else "Catálogo no disponible temporalmente."
        _cache["data"] = ctx
        _cache["ts"] = now
        return ctx

    except Exception as e:
        return f"[No se pudo cargar el catálogo: {e}]"


def _size_reference(body_html: str) -> str:
    """' | Referencia de talla: ...' si la descripción la trae; si no, cadena vacía."""
    text = re.sub(r"<(style|script)[^>]*>.*?</\1>", " ", body_html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    m = re.search(r"Referencia de talla:\s*(.{0,160}?)(?:\.\s|$)", text, flags=re.I)
    if not m:
        return ""
    ref = m.group(1).strip().rstrip(".")
    return f" | Referencia de talla: {ref}" if ref else ""


def get_product_image(handle: str) -> str | None:
    """Return the first image URL for a product handle, or None."""
    return _image_map.get(handle)
