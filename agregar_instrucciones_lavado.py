"""
agregar_instrucciones_lavado.py
--------------------------------
Agrega la guía de cuidado de prendas al final de la descripción de cada
producto activo en Shopify, SIN sobreescribir el contenido existente.

Seguridad:
- Usa un marcador único para no duplicar si se corre dos veces.
- Muestra un preview antes de aplicar y pide confirmación.
- Respeta el rate limit de Shopify (máx 2 req/seg).
"""

import os
import time
import requests
from dotenv import load_dotenv

load_dotenv()

STORE_URL   = os.getenv("SHOPIFY_STORE_URL")
ADMIN_TOKEN = os.getenv("SHOPIFY_ADMIN_TOKEN")
API_VERSION = "2025-01"

# Marcador para detectar si ya fue agregado (no duplicar)
MARKER = "<!-- nativa-care-guide -->"

CARE_GUIDE_HTML = f"""{MARKER}
<hr>
<h3>🧼 Cuidado de tu prenda</h3>
<ul>
  <li>Lavar en frío (máx. 30°C), ciclo suave, prenda al revés</li>
  <li>Usar detergente suave — sin cloro ni blanqueadores</li>
  <li>No mezclar con prendas ásperas (jeans, velcro, cierres)</li>
  <li>No usar secadora — secar al aire libre a la sombra</li>
  <li>No planchar directamente sobre el estampado (hacerlo por el reverso)</li>
</ul>
<p><em>Las primeras lavadas son clave: lavar sola o con colores similares.</em></p>
"""


def headers():
    return {
        "X-Shopify-Access-Token": ADMIN_TOKEN,
        "Content-Type": "application/json",
    }


def get_all_products():
    url = f"https://{STORE_URL}/admin/api/{API_VERSION}/products.json"
    params = {"limit": 250, "status": "active", "fields": "id,title,body_html"}
    resp = requests.get(url, headers=headers(), params=params, timeout=15)
    resp.raise_for_status()
    return resp.json().get("products", [])


def update_product_description(product_id, new_body):
    url = f"https://{STORE_URL}/admin/api/{API_VERSION}/products/{product_id}.json"
    payload = {"product": {"id": product_id, "body_html": new_body}}
    resp = requests.put(url, headers=headers(), json=payload, timeout=15)
    resp.raise_for_status()
    return resp.json()


def main():
    print("Obteniendo productos de Shopify...")
    products = get_all_products()
    print(f"Total productos activos: {len(products)}\n")

    to_update = []
    already_done = []

    for p in products:
        body = p.get("body_html") or ""
        if MARKER in body:
            already_done.append(p["title"])
        else:
            to_update.append(p)

    if already_done:
        print(f"Ya tienen instrucciones ({len(already_done)} productos): omitidos.")

    if not to_update:
        print("Todos los productos ya tienen las instrucciones de cuidado. Nada que hacer.")
        return

    print(f"\nProductos a actualizar ({len(to_update)}):")
    for p in to_update:
        print(f"  - {p['title']}")

    print(f"\nSe agregará la guía de cuidado al FINAL de la descripción de cada uno.")
    confirm = input("\n¿Confirmas? Escribe 'SI' para continuar: ").strip().upper()
    if confirm != "SI":
        print("Cancelado.")
        return

    ok = 0
    errors = []
    for p in to_update:
        try:
            body = p.get("body_html") or ""
            new_body = body + "\n" + CARE_GUIDE_HTML
            update_product_description(p["id"], new_body)
            print(f"  ✓ {p['title']}")
            ok += 1
            time.sleep(0.6)  # respetar rate limit Shopify
        except Exception as e:
            print(f"  ✗ {p['title']}: {e}")
            errors.append(p["title"])

    print(f"\nListo: {ok} productos actualizados.")
    if errors:
        print(f"Errores en {len(errors)} productos: {errors}")


if __name__ == "__main__":
    main()
