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
from whatsapp_client import send_template

load_dotenv()

# ── Constants ────────────────────────────────────────────────────────────────

RECOVERY_DELAY   = 45 * 60       # 45 min → first message
FOLLOWUP_DELAY   = 24 * 60 * 60  # 24 h  → second message (returning customers)

# Templates (nombres exactos aprobados en Meta)
TEMPLATE_FIRST_TIME = "antiguo_con_codigo"      # sin compra previa
TEMPLATE_RETURNING  = "carrito_clientes_nuevo"  # compró al menos una vez
TEMPLATE_FOLLOWUP   = "cliente_nuevo2_"         # seguimiento 24 h (returning)


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
    Infinite loop checking abandoned carts every 60 seconds.

    Pass 1 — status='pending', older than 45 min:
      - No phone → mark no_phone
      - Returning customer (has completed order) → send TEMPLATE_RETURNING, mark sent_followup_pending
      - First-time buyer → send TEMPLATE_FIRST_TIME, mark sent

    Pass 2 — status='sent_followup_pending', message_sent_at older than 24 h:
      - Send TEMPLATE_FOLLOWUP, mark sent
    """
    print("[cart_recovery] Recovery scheduler started.")

    while True:
        try:
            now = time.time()
            db  = get_db()
            try:
                # ── Pass 1: first message ─────────────────────────────────
                pending = db.execute(
                    """
                    SELECT token, phone, name, products
                    FROM   abandoned_carts
                    WHERE  status = 'pending'
                    AND    created_at < ?
                    """,
                    (now - RECOVERY_DELAY,),
                ).fetchall()

                print(f"[cart_recovery] Checking pending carts — found {len(pending)} eligible.")

                for row in pending:
                    token = row["token"]
                    phone = row["phone"]
                    first_name = (row["name"] or "").split()[0] or "amig@"

                    try:
                        if not phone:
                            db.execute("UPDATE abandoned_carts SET status='no_phone' WHERE token=?", (token,))
                            db.commit()
                            continue

                        products_text = format_products(row["products"])
                        completed = db.execute(
                            "SELECT 1 FROM completed_orders WHERE phone=? LIMIT 1", (phone,)
                        ).fetchone()

                        if completed:
                            # Returning customer — send carrito_clientes_nuevo + schedule follow-up
                            send_template(phone, TEMPLATE_RETURNING, [first_name, products_text])
                            db.execute(
                                "UPDATE abandoned_carts SET status='sent_followup_pending', message_sent_at=? WHERE token=?",
                                (now, token),
                            )
                            print(f"[cart_recovery] Cart {token}: returning customer template sent to {phone}.")
                        else:
                            # First-time buyer — send antiguo_con_codigo
                            send_template(phone, TEMPLATE_FIRST_TIME, [first_name, products_text])
                            db.execute(
                                "UPDATE abandoned_carts SET status='sent', message_sent_at=? WHERE token=?",
                                (now, token),
                            )
                            print(f"[cart_recovery] Cart {token}: first-time template sent to {phone}.")

                        db.commit()

                    except Exception as exc:
                        print(f"[cart_recovery] ERROR processing cart {token}: {exc}")
                        try:
                            db.execute("UPDATE abandoned_carts SET status='error' WHERE token=?", (token,))
                            db.commit()
                        except Exception:
                            pass

                # ── Pass 2: follow-up for returning customers ─────────────
                followups = db.execute(
                    """
                    SELECT token, phone, name
                    FROM   abandoned_carts
                    WHERE  status = 'sent_followup_pending'
                    AND    message_sent_at < ?
                    """,
                    (now - FOLLOWUP_DELAY,),
                ).fetchall()

                for row in followups:
                    token      = row["token"]
                    phone      = row["phone"]
                    first_name = (row["name"] or "").split()[0] or "amig@"
                    try:
                        send_template(phone, TEMPLATE_FOLLOWUP, [first_name])
                        db.execute(
                            "UPDATE abandoned_carts SET status='sent', message_sent_at=? WHERE token=?",
                            (now, token),
                        )
                        db.commit()
                        print(f"[cart_recovery] Cart {token}: follow-up template sent to {phone}.")
                    except Exception as exc:
                        print(f"[cart_recovery] ERROR sending follow-up for cart {token}: {exc}")

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
