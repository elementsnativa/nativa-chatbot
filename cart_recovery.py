"""
cart_recovery.py — Abandoned cart recovery scheduler for Nativa Elements.

Runs a background daemon thread that wakes up every 60 seconds and processes
carts that have been abandoned for longer than RECOVERY_DELAY seconds.

Flow per cart:
  1. Skip if the phone already has a completed order → mark 'converted'
  2. Skip if no valid phone → mark 'no_phone'
  3. Send WhatsApp recovery message → mark 'sent'
  4. On any error → mark 'error' (with log)

Call start_recovery_scheduler() once at app startup.
"""

import json
import threading
import time

from dotenv import load_dotenv

from database import get_db
from whatsapp_client import send_text

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────

RECOVERY_DELAY = 45 * 60  # 45 minutes in seconds

RECOVERY_MSG = (
    "👋 Hola {name}, soy Nati de Nativa Elements.\n\n"
    "Vimos que dejaste estos productos en tu carrito:\n"
    "{products}\n\n"
    "¿Tienes alguna duda sobre tallas, envío o medios de pago? "
    "Con gusto te ayudo para que puedas completar tu compra. "
    "Tu carrito sigue guardado y te esperamos 🙂"
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_products(products_json: str) -> str:
    """
    Parse a JSON list of {title, price} dicts and return a bullet list.
    Shows at most 3 items. Prices are formatted as Chilean pesos.

    Example output:
      • Polera Trail Run — $24.990
      • Short Outdoor — $19.990
      • +1 producto más
    """
    try:
        items = json.loads(products_json or "[]")
    except (json.JSONDecodeError, TypeError):
        return "• (productos no disponibles)"

    if not items:
        return "• (carrito vacío)"

    lines = []
    shown = items[:3]
    for item in shown:
        title = item.get("title", "Producto")
        try:
            price_raw = float(str(item.get("price", 0)).replace(",", "."))
            price_str = f"${price_raw:,.0f}".replace(",", ".")
        except (ValueError, TypeError):
            price_str = str(item.get("price", ""))
        lines.append(f"• {title} — {price_str}")

    remaining = len(items) - len(shown)
    if remaining > 0:
        lines.append(f"  +{remaining} producto{'s' if remaining > 1 else ''} más")

    return "\n".join(lines)


# ── Core recovery loop ────────────────────────────────────────────────────────

def process_pending_recoveries() -> None:
    """
    Infinite loop that checks for pending abandoned carts every 60 seconds.
    Designed to run in a background daemon thread.
    """
    print("[cart_recovery] Recovery scheduler started.")

    while True:
        try:
            cutoff = time.time() - RECOVERY_DELAY
            db = get_db()

            try:
                rows = db.execute(
                    """
                    SELECT token, phone, name, products, checkout_url, total
                    FROM   abandoned_carts
                    WHERE  status = 'pending'
                    AND    created_at < ?
                    """,
                    (cutoff,),
                ).fetchall()

                print(f"[cart_recovery] Checking pending carts — found {len(rows)} eligible.")

                for row in rows:
                    token = row["token"]
                    phone = row["phone"]
                    name = row["name"] or "amig@"

                    try:
                        # ── No phone on record ─────────────────────────────
                        if not phone:
                            print(f"[cart_recovery] Cart {token}: no phone, marking no_phone.")
                            db.execute(
                                "UPDATE abandoned_carts SET status='no_phone' WHERE token=?",
                                (token,),
                            )
                            db.commit()
                            continue

                        # ── Check if order was already completed ───────────
                        completed = db.execute(
                            "SELECT 1 FROM completed_orders WHERE phone=? LIMIT 1",
                            (phone,),
                        ).fetchone()

                        if completed:
                            print(f"[cart_recovery] Cart {token}: phone {phone} already converted.")
                            db.execute(
                                "UPDATE abandoned_carts SET status='converted' WHERE token=?",
                                (token,),
                            )
                            db.commit()
                            continue

                        # ── Send recovery message ──────────────────────────
                        products_text = format_products(row["products"])
                        message = RECOVERY_MSG.format(
                            name=name.split()[0] if name else "amig@",
                            products=products_text,
                        )

                        send_text(phone, message)

                        db.execute(
                            """
                            UPDATE abandoned_carts
                            SET    status='sent', message_sent_at=?
                            WHERE  token=?
                            """,
                            (time.time(), token),
                        )
                        db.commit()
                        print(f"[cart_recovery] Cart {token}: recovery message sent to {phone}.")

                    except Exception as item_exc:
                        print(f"[cart_recovery] ERROR processing cart {token}: {item_exc}")
                        try:
                            db.execute(
                                "UPDATE abandoned_carts SET status='error' WHERE token=?",
                                (token,),
                            )
                            db.commit()
                        except Exception as db_exc:
                            print(f"[cart_recovery] ERROR marking cart {token} as error: {db_exc}")

            finally:
                db.close()

        except Exception as loop_exc:
            print(f"[cart_recovery] ERROR in recovery loop: {loop_exc}")

        time.sleep(60)


# ── Scheduler bootstrap ───────────────────────────────────────────────────────

def start_recovery_scheduler() -> None:
    """
    Start the abandoned-cart recovery loop in a background daemon thread.
    Call this once during application startup (e.g. FastAPI lifespan handler).
    """
    thread = threading.Thread(
        target=process_pending_recoveries,
        name="cart-recovery-scheduler",
        daemon=True,
    )
    thread.start()
    print(f"[cart_recovery] Daemon thread '{thread.name}' launched (pid-agnostic).")
