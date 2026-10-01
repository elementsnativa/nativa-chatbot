"""
cart_tracking.py — what happens to a cart recovery message after it is sent.

A send is only the beginning. This module records the three things that say
whether the campaign actually worked:

  record_statuses()  delivery lifecycle (delivered / read / failed), from the
                     WhatsApp status webhook
  record_click()     a tap on the message button, which WhatsApp does NOT
                     report — it is only observable because the button points
                     at our own redirect, /c/{token}
  record_reply()     what the customer wrote back, and how long they took

Every function swallows its own errors: none of this is worth failing a webhook
or blocking a reply to a customer over.
"""

import time

from database import get_db

# A reply is attributed to a recovery message only within this window. Past it,
# the customer is writing about something else.
REPLY_ATTRIBUTION_WINDOW = 7 * 86400


def record_statuses(statuses: list) -> None:
    """
    Apply a batch of WhatsApp message statuses to the sends they belong to.

    Statuses arrive for every message the number sends, most of them unrelated
    to cart recovery, so a status whose message_id matches no send is ignored.
    Each column is only ever set once: WhatsApp can resend a status, and the
    first timestamp is the true one.
    """
    if not statuses:
        return

    db = get_db()
    try:
        for status in statuses:
            wamid = status.get("id")
            state = status.get("status")
            if not wamid or not state:
                continue
            try:
                ts = float(status.get("timestamp") or time.time())
            except (TypeError, ValueError):
                ts = time.time()

            column = {"delivered": "delivered_at", "read": "read_at", "failed": "failed_at"}.get(state)
            if not column:
                continue

            error_code = None
            if state == "failed":
                errors = status.get("errors") or [{}]
                error_code = str(errors[0].get("code") or "")[:40] or None

            try:
                if error_code:
                    db.execute(
                        f"UPDATE recovery_sends SET {column} = ?, error_code = ? "
                        f"WHERE message_id = ? AND {column} IS NULL",
                        (ts, error_code, wamid),
                    )
                else:
                    db.execute(
                        f"UPDATE recovery_sends SET {column} = ? "
                        f"WHERE message_id = ? AND {column} IS NULL",
                        (ts, wamid),
                    )
                db.commit()
            except Exception as exc:
                print(f"[cart_tracking] WARNING: could not store status {state} for {wamid}: {exc}")
                try:
                    db.rollback()
                except Exception:
                    pass
    except Exception as exc:
        print(f"[cart_tracking] WARNING: record_statuses failed: {exc}")
    finally:
        db.close()


def record_click(cart_token: str) -> str | None:
    """
    Register a button tap for a cart and return where to send the customer.

    Credits the most recent send for that cart, so a tap on yesterday's message
    is not attributed to today's. Returns the checkout URL, or None when the
    token is unknown.
    """
    now = time.time()
    db = get_db()
    try:
        row = db.execute(
            "SELECT checkout_url FROM abandoned_carts WHERE token = ?", (cart_token,)
        ).fetchone()
        if not row:
            return None

        try:
            db.execute(
                """
                UPDATE recovery_sends
                SET    clicked_at = COALESCE(clicked_at, ?), click_count = click_count + 1
                WHERE  id = (
                    SELECT id FROM recovery_sends
                    WHERE cart_token = ? ORDER BY sent_at DESC LIMIT 1
                )
                """,
                (now, cart_token),
            )
            db.commit()
        except Exception as exc:
            print(f"[cart_tracking] WARNING: could not store click for {cart_token}: {exc}")
            try:
                db.rollback()
            except Exception:
                pass

        return row["checkout_url"] or None
    except Exception as exc:
        print(f"[cart_tracking] WARNING: record_click failed: {exc}")
        return None
    finally:
        db.close()


def record_reply(phone: str, body: str, replied_at: float | None = None) -> None:
    """
    Store an incoming message as a reply to a recovery campaign, when the phone
    received one recently.

    Called for every inbound message, so the recency check is what keeps
    ordinary support conversations out of the campaign numbers.
    """
    if not phone or not body:
        return

    now = float(replied_at or time.time())
    db = get_db()
    try:
        send = db.execute(
            """
            SELECT cart_token, stage, template, sent_at
            FROM   recovery_sends
            WHERE  phone = ? AND sent_at <= ? AND ? - sent_at <= ?
            ORDER  BY sent_at DESC
            LIMIT  1
            """,
            (phone, now, now, REPLY_ATTRIBUTION_WINDOW),
        ).fetchone()
        if not send:
            return

        db.execute(
            """
            INSERT INTO recovery_replies
                (phone, cart_token, stage, template, body, replied_at, latency_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (phone, send["cart_token"], send["stage"], send["template"],
             body[:2000], now, now - float(send["sent_at"])),
        )
        db.commit()
        print(f"[cart_tracking] Reply to stage {send['stage']} recorded for {phone}.")
    except Exception as exc:
        print(f"[cart_tracking] WARNING: record_reply failed: {exc}")
        try:
            db.rollback()
        except Exception:
            pass
    finally:
        db.close()
