"""
cart_recovery.py — Abandoned cart recovery scheduler for Nativa Elements.

A background daemon wakes up every POLL_INTERVAL seconds and walks each cart
through a three-message sequence. Every message carries the customer's first
name, a one-line summary of what they left behind, and a URL button pointing
straight at their checkout.

Sequence, measured from the moment the cart was abandoned:

    stage 1 —  1 h  → reminder, no discount
    stage 2 — 24 h  → reminder + 10% code
    stage 3 — 72 h  → last call, invites a reply

Template names and language live in bot_config so the admin panel can change
them without a deploy; the keys are cart_stage1_template, cart_stage2_template,
cart_stage3_template and cart_template_lang.

Guard rails, each one a failure this code has actually produced:

  - Parameters are single-line. Meta rejects new-lines and tabs inside a
    parameter with error 132000, which is why the sends were silently emptied
    to [] and customers received messages with no name, products or link.
  - Quiet hours in the store timezone, so no cart ever messages anyone at 3 AM.
  - A per-phone cooldown, so someone who abandons five carts in a week is not
    messaged fifteen times.
  - The sequence stops the moment the customer buys.
  - A cart with no usable checkout URL is skipped rather than sent as a
    dead end.
  - Only carts older than SKIP_BACKLOG_AFTER are flushed at startup, so a
    redeploy no longer discards carts that are still worth recovering — while
    still preventing the mass send that happened when a backlog built up.

Everything stays off unless CART_RECOVERY_ENABLED is truthy.

Environment variables:
  CART_RECOVERY_ENABLED   "true" to arm the scheduler            (default: false)
  REQUIRE_OPT_IN          "true" to send only to opted-in phones (default: false)
  STORE_PUBLIC_DOMAIN     domain the templates' URL button uses  (default: www.nativaelements.com)
  STORE_TIMEZONE          IANA timezone for quiet hours          (default: America/Santiago)
  QUIET_START_HOUR        hour messaging stops, 0-23             (default: 21)
  QUIET_END_HOUR          hour messaging resumes, 0-23           (default: 9)
  RECOVERY_COOLDOWN_DAYS  days before re-contacting a phone      (default: 14)
  SKIP_BACKLOG_HOURS      age past which a cart is dropped       (default: 6)
  RECOVERY_STAGES         how many of the 3 stages to run        (default: 3)

Call start_recovery_scheduler() once at app startup.
"""

import json
import os
import re
import threading
import time
from datetime import datetime
from zoneinfo import ZoneInfo

from dotenv import load_dotenv

from database import get_db
from whatsapp_client import send_template

load_dotenv()


# ── Configuration ─────────────────────────────────────────────────────────────

def _flag(name: str, default: str = "false") -> bool:
    return os.getenv(name, default).strip().lower() in {"1", "true", "yes", "on"}


ENABLED            = _flag("CART_RECOVERY_ENABLED")
REQUIRE_OPT_IN     = _flag("REQUIRE_OPT_IN")
STORE_DOMAIN       = os.getenv("STORE_PUBLIC_DOMAIN", "www.nativaelements.com").strip().rstrip("/")
COOLDOWN_DAYS      = float(os.getenv("RECOVERY_COOLDOWN_DAYS", "14"))
QUIET_START        = int(os.getenv("QUIET_START_HOUR", "21"))
QUIET_END          = int(os.getenv("QUIET_END_HOUR", "9"))
SKIP_BACKLOG_AFTER = float(os.getenv("SKIP_BACKLOG_HOURS", "6")) * 3600
POLL_INTERVAL      = 60

try:
    STORE_TZ = ZoneInfo(os.getenv("STORE_TIMEZONE", "America/Santiago"))
except Exception:  # pragma: no cover — no tzdata on a slim image
    STORE_TZ = None
    print("[cart_recovery] WARNING: timezone unavailable, quiet hours disabled.")

# (stage number, delay since abandonment, bot_config key, fallback template name)
_ALL_STAGES: list[tuple[int, int, str, str]] = [
    (1,  1 * 3600, "cart_stage1_template", "carrito_abandonado"),
    (2, 24 * 3600, "cart_stage2_template", "carrito_24h"),
    (3, 72 * 3600, "cart_stage3_template", "carrito_72h"),
]

# How many stages to actually run. A stage whose template is not yet approved
# would fail every send and park the cart in 'error', so the sequence can be
# shortened while a template is still in review and lengthened later without a
# deploy.
STAGES = _ALL_STAGES[: max(1, min(int(os.getenv("RECOVERY_STAGES", "3")), len(_ALL_STAGES)))]
FINAL_STAGE = STAGES[-1][0]

_WHITESPACE = re.compile(r"\s+")


# ── bot_config ────────────────────────────────────────────────────────────────

def get_config(db, key: str, fallback: str) -> str:
    """Read a bot_config value, falling back when the row is missing."""
    try:
        row = db.execute("SELECT value FROM bot_config WHERE key = ?", (key,)).fetchone()
        if row and row["value"]:
            return str(row["value"]).strip()
    except Exception as exc:
        print(f"[cart_recovery] WARNING: could not read config '{key}': {exc}")
    return fallback


# ── Helpers ───────────────────────────────────────────────────────────────────

def format_products(products_json: str, max_items: int = 3) -> str:
    """
    Turn the stored JSON list of {title, price} into ONE line fit for a template
    parameter. Deliberately a sentence rather than a bullet list, because Meta
    rejects parameters containing new-lines:

        "Polera Trail Run, Short Outdoor y 2 productos más"
    """
    try:
        items = json.loads(products_json or "[]")
    except (json.JSONDecodeError, TypeError):
        items = []

    titles = [
        _WHITESPACE.sub(" ", str(item.get("title", "")).strip())
        for item in items
        if isinstance(item, dict) and item.get("title")
    ]
    if not titles:
        return "tu selección"

    shown, remaining = titles[:max_items], len(titles) - max_items
    if remaining > 0:
        return f"{', '.join(shown)} y {remaining} producto{'s' if remaining > 1 else ''} más"
    if len(shown) == 1:
        return shown[0]
    return f"{', '.join(shown[:-1])} y {shown[-1]}"


def first_name_of(full_name: str) -> str:
    """First name, capitalised, with a neutral fallback."""
    cleaned = _WHITESPACE.sub(" ", str(full_name or "")).strip()
    if not cleaned:
        return "hola"
    return cleaned.split(" ")[0][:40].capitalize()


def checkout_button_suffix(checkout_url: str) -> str | None:
    """
    Extract the part of the checkout URL that fills the template's dynamic URL
    button. Meta's URL buttons are a fixed base plus a trailing variable —
    https://www.nativaelements.com/{{1}} — so only path and query travel in the
    message. Returns None when the URL is missing or unusable.
    """
    url = (checkout_url or "").strip()
    if not url:
        return None
    match = re.match(r"https?://[^/]+/(.+)", url)
    return match.group(1) if match else None


def within_quiet_hours(now_ts: float) -> bool:
    """True when local time falls inside the do-not-disturb window."""
    if STORE_TZ is None or QUIET_START == QUIET_END:
        return False
    hour = datetime.fromtimestamp(now_ts, STORE_TZ).hour
    if QUIET_START < QUIET_END:
        return QUIET_START <= hour < QUIET_END
    return hour >= QUIET_START or hour < QUIET_END   # window crosses midnight


def recently_contacted(db, phone: str, cart_token: str, now_ts: float) -> bool:
    """
    True when this phone already received a recovery message for a DIFFERENT
    cart inside the cooldown window. Messages within one cart's own sequence are
    not cooldown-limited; the stage delays already space those out.
    """
    if COOLDOWN_DAYS <= 0:
        return False
    row = db.execute(
        """
        SELECT 1 FROM recovery_sends
        WHERE  phone = ? AND cart_token != ? AND sent_at > ?
        LIMIT  1
        """,
        (phone, cart_token, now_ts - COOLDOWN_DAYS * 86400),
    ).fetchone()
    return row is not None


def has_purchased(db, phone: str, since_ts: float) -> bool:
    """True when this phone completed an order after the cart was created."""
    row = db.execute(
        "SELECT 1 FROM completed_orders WHERE phone = ? AND completed_at >= ? LIMIT 1",
        (phone, since_ts),
    ).fetchone()
    return row is not None


# ── Sending ───────────────────────────────────────────────────────────────────

def send_stage(db, cart, stage: int, template: str, language: str, now_ts: float) -> None:
    """Send one stage of the sequence and record it. Raises on failure."""
    phone = cart["phone"]
    body_params = [first_name_of(cart["name"]), format_products(cart["products"])]
    suffix = checkout_button_suffix(cart["checkout_url"])

    response = send_template(
        phone,
        template,
        body_params,
        language=language,
        button_params=[suffix],
    )
    message_id = (response.get("messages") or [{}])[0].get("id")

    db.execute(
        """
        UPDATE abandoned_carts
        SET    stage = ?, last_sent_at = ?, message_sent_at = ?, status = ?
        WHERE  token = ?
        """,
        (stage, now_ts, now_ts, "done" if stage >= FINAL_STAGE else "pending", cart["token"]),
    )
    db.execute(
        """
        INSERT INTO recovery_sends (phone, cart_token, stage, template, message_id, sent_at)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (phone, cart["token"], stage, template, message_id, now_ts),
    )
    db.commit()
    print(f"[cart_recovery] Cart {cart['token']}: stage {stage} ({template}) sent to {phone}.")


def process_cart(db, cart, now_ts: float, language: str) -> None:
    """Evaluate one cart and send its next message if every check passes."""
    token = cart["token"]
    stage = cart["stage"] or 0

    def mark(status: str, reason: str) -> None:
        db.execute("UPDATE abandoned_carts SET status = ? WHERE token = ?", (status, token))
        db.commit()
        print(f"[cart_recovery] Cart {token}: {reason} → status={status}.")

    if not cart["phone"]:
        return mark("no_phone", "no usable phone number")

    if not checkout_button_suffix(cart["checkout_url"]):
        return mark("no_url", "no usable checkout URL — refusing to send a dead end")

    if REQUIRE_OPT_IN and not cart["accepts_marketing"]:
        return mark("no_opt_in", "customer did not consent to marketing messages")

    if has_purchased(db, cart["phone"], cart["created_at"] or 0):
        return mark("converted", "customer already purchased")

    next_stage, delay, config_key, fallback = STAGES[stage]

    # The clock runs from abandonment for stage 1 and from the previous message
    # afterwards, so a backlog never fires three messages back to back.
    reference = cart["created_at"] if stage == 0 else (cart["last_sent_at"] or cart["created_at"])
    previous_delay = 0 if stage == 0 else STAGES[stage - 1][1]
    if now_ts - reference < delay - previous_delay:
        return

    if recently_contacted(db, cart["phone"], token, now_ts):
        return mark("skipped_cooldown", f"phone contacted within {COOLDOWN_DAYS:g} days")

    send_stage(db, cart, next_stage, get_config(db, config_key, fallback), language, now_ts)


# ── Core loop ─────────────────────────────────────────────────────────────────

def process_pending_recoveries() -> None:
    """Poll for carts due a message until the process exits."""
    print(
        f"[cart_recovery] Scheduler started — {len(STAGES)} stage(s), "
        f"quiet {QUIET_START}:00-{QUIET_END}:00, "
        f"cooldown {COOLDOWN_DAYS:g}d, opt-in required={REQUIRE_OPT_IN}, domain {STORE_DOMAIN}."
    )

    while True:
        try:
            now_ts = time.time()

            if within_quiet_hours(now_ts):
                time.sleep(POLL_INTERVAL)
                continue

            db = get_db()
            try:
                language = get_config(db, "cart_template_lang", "es")
                due = db.execute(
                    """
                    SELECT token, phone, name, products, checkout_url, created_at,
                           stage, last_sent_at, accepts_marketing
                    FROM   abandoned_carts
                    WHERE  status = 'pending' AND COALESCE(stage, 0) < ?
                    ORDER  BY created_at
                    LIMIT  200
                    """,
                    (FINAL_STAGE,),
                ).fetchall()

                for cart in due:
                    try:
                        process_cart(db, cart, now_ts, language)
                    except Exception as exc:
                        print(f"[cart_recovery] ERROR on cart {cart['token']}: {exc}")
                        try:
                            # PostgreSQL aborts the transaction on a failed
                            # statement; clear it before recording the failure.
                            db.rollback()
                            db.execute(
                                "UPDATE abandoned_carts SET status = 'error' WHERE token = ?",
                                (cart["token"],),
                            )
                            db.commit()
                        except Exception:
                            pass
            finally:
                db.close()

        except Exception as loop_exc:
            print(f"[cart_recovery] ERROR in recovery loop: {loop_exc}")

        time.sleep(POLL_INTERVAL)


# ── Scheduler bootstrap ───────────────────────────────────────────────────────

def _skip_stale_pending() -> None:
    """
    Drop carts too old to be worth recovering, once at startup.

    The previous version skipped EVERY pending cart on every boot, which on
    Railway meant each redeploy threw away carts abandoned minutes earlier. It
    existed to stop a mass send when a backlog built up, so the backlog defence
    is kept — bounded by age instead of catching everything.
    """
    cutoff = time.time() - SKIP_BACKLOG_AFTER
    db = get_db()
    try:
        result = db.execute(
            "UPDATE abandoned_carts SET status = 'skipped' WHERE status = 'pending' AND created_at < ?",
            (cutoff,),
        )
        db.commit()
        print(
            f"[cart_recovery] Skipped {result.rowcount} cart(s) older than "
            f"{SKIP_BACKLOG_AFTER / 3600:g}h; newer ones kept."
        )
    except Exception as exc:
        print(f"[cart_recovery] WARNING: could not skip stale carts: {exc}")
    finally:
        db.close()


def start_recovery_scheduler() -> None:
    """
    Start the recovery loop in a background daemon thread.

    Does nothing unless CART_RECOVERY_ENABLED is truthy, so the service can be
    deployed and tested without messaging a single customer.
    """
    if not ENABLED:
        print("[cart_recovery] DISABLED — set CART_RECOVERY_ENABLED=true to arm it.")
        return

    _skip_stale_pending()
    thread = threading.Thread(
        target=process_pending_recoveries,
        name="cart-recovery-scheduler",
        daemon=True,
    )
    thread.start()
    print(f"[cart_recovery] Daemon thread '{thread.name}' launched.")
