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

            # Descripción limpia (sin HTML, máx 200 chars)
            raw_desc = p.get("body_html") or ""
            desc = re.sub(r"<[^>]+>", " ", raw_desc)
            desc = re.sub(r"\s+", " ", desc).strip()[:200]
            desc_part = f" | {desc}" if desc else ""

            lines.append(
                f"- {p['title']}: ${price:,.0f} CLP | {tallas}{desc_part} | {product_url}"
            )

        ctx = "\n".join(lines) if lines else "Catálogo no disponible temporalmente."
        _cache["data"] = ctx
        _cache["ts"] = now
        return ctx

    except Exception as e:
        return f"[No se pudo cargar el catálogo: {e}]"


def get_product_image(handle: str) -> str | None:
    """Return the first image URL for a product handle, or None."""
    return _image_map.get(handle)
