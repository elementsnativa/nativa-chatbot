"""
instagram_client.py — Meta Instagram Messaging API client for Nativa Elements.

Provides:
  - send_text(recipient_id, text)   → POST a plain-text message via the Graph API
  - send_image(recipient_id, url)   → POST an image attachment
  - refresh_token()                 → extend the long-lived token for 60 more days
  - start_token_refresh_scheduler() → keep it refreshed automatically

Instagram long-lived tokens expire after 60 days, silently: the bot simply
stops replying and nothing surfaces the reason except a 190 in the logs.
The scheduler refreshes the token daily and stores the result in the
bot_config table, so it survives restarts and never reaches that deadline.

The token travels in the Authorization header, never as a query parameter,
so it does not end up written in plain text in the deploy logs.

Environment variables:
  INSTAGRAM_PAGE_TOKEN   — long-lived Instagram token (the starting point;
                           once refreshed, the stored one takes over)
  INSTAGRAM_VERIFY_TOKEN — Webhook verify token (defaults to "nativa2024secure")
"""

import os
import re
import threading
import time

import requests
from dotenv import load_dotenv

from database import get_db

load_dotenv()

INSTAGRAM_PAGE_TOKEN = os.getenv("INSTAGRAM_PAGE_TOKEN")
VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "nativa2024secure")
API_VERSION = "v25.0"

_BASE_URL = f"https://graph.instagram.com/{API_VERSION}/me/messages"
_REFRESH_URL = "https://graph.instagram.com/refresh_access_token"

# bot_config keys
_TOKEN_KEY = "instagram_token"        # the live token (refreshed over time)
_SEED_KEY = "instagram_token_seed"    # env value the stored token came from

REFRESH_INTERVAL = 24 * 3600          # try once a day
_MIN_TOKEN_AGE_HINT = "must be at least 24 hours old"

_token_cache: str | None = None
_token_lock = threading.Lock()


# ── Token storage ─────────────────────────────────────────────────────────────

def _redact(text) -> str:
    """Strip anything that looks like a Meta token out of *text*."""
    return re.sub(r"(IGAA|EAA)[A-Za-z0-9_\-]{20,}", "<token redacted>", str(text))


def _read_config(key: str) -> str | None:
    db = get_db()
    try:
        row = db.execute("SELECT value FROM bot_config WHERE key=?", (key,)).fetchone()
        return row["value"] if row else None
    except Exception as exc:
        print(f"[instagram_client] WARNING: could not read {key}: {_redact(exc)}")
        return None
    finally:
        db.close()


def _write_config(key: str, value: str) -> None:
    db = get_db()
    try:
        db.execute(
            """
            INSERT INTO bot_config (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT (key) DO UPDATE SET
                value      = excluded.value,
                updated_at = excluded.updated_at
            """,
            (key, value, time.time()),
        )
        db.commit()
    except Exception as exc:
        print(f"[instagram_client] WARNING: could not save {key}: {_redact(exc)}")
    finally:
        db.close()


def _get_token() -> str | None:
    """Return the token in use: the refreshed one, unless the env var changed.

    If INSTAGRAM_PAGE_TOKEN no longer matches the seed the stored token grew
    from, someone pasted a new token in Railway — that manual override wins,
    otherwise a stale stored token would silently ignore the fix.
    """
    global _token_cache
    with _token_lock:
        if _token_cache:
            return _token_cache

        stored = _read_config(_TOKEN_KEY)
        seed = _read_config(_SEED_KEY)

        if stored and seed == INSTAGRAM_PAGE_TOKEN:
            _token_cache = stored
            print("[instagram_client] Using refreshed token from bot_config.")
        else:
            _token_cache = INSTAGRAM_PAGE_TOKEN
            if INSTAGRAM_PAGE_TOKEN:
                print("[instagram_client] Using INSTAGRAM_PAGE_TOKEN from the environment.")
                _write_config(_TOKEN_KEY, INSTAGRAM_PAGE_TOKEN)
                _write_config(_SEED_KEY, INSTAGRAM_PAGE_TOKEN)

        return _token_cache


def _auth_headers() -> dict:
    token = _get_token()
    if not token:
        raise RuntimeError("[instagram_client] INSTAGRAM_PAGE_TOKEN is not set")
    return {"Authorization": f"Bearer {token}"}


# ── Sending ───────────────────────────────────────────────────────────────────

def _post(payload: dict, what: str, recipient_id: str) -> dict:
    try:
        resp = requests.post(_BASE_URL, json=payload, headers=_auth_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        body = exc.response.text if exc.response is not None else ""
        print(f"[instagram_client] HTTP error sending {what} to {recipient_id}: "
              f"{_redact(exc)} — body: {_redact(body)}")
        if '"code":190' in body:
            print("[instagram_client] ERROR: the Instagram token is invalid or expired. "
                  "Generate a new one in the Meta app and update INSTAGRAM_PAGE_TOKEN.")
        raise
    except Exception as exc:
        print(f"[instagram_client] Unexpected error sending {what} to {recipient_id}: {_redact(exc)}")
        raise


def send_text(recipient_id: str, text: str) -> dict:
    """Send a plain-text Instagram DM to *recipient_id* (Instagram PSID)."""
    data = _post(
        {
            "recipient": {"id": recipient_id},
            "message": {"text": text},
            "messaging_type": "RESPONSE",
        },
        "message",
        recipient_id,
    )
    print(f"[instagram_client] Message sent to {recipient_id} — mid={data.get('message_id')}")
    return data


def send_image(recipient_id: str, image_url: str) -> dict:
    """Send an image attachment via Instagram DM."""
    return _post(
        {
            "recipient": {"id": recipient_id},
            "message": {
                "attachment": {
                    "type": "image",
                    "payload": {"url": image_url, "is_reusable": True},
                }
            },
            "messaging_type": "RESPONSE",
        },
        "image",
        recipient_id,
    )


# ── Token refresh ─────────────────────────────────────────────────────────────

def refresh_token() -> bool:
    """Extend the long-lived token by 60 days and store the result.

    Returns True when the token was refreshed and saved.
    """
    global _token_cache

    token = _get_token()
    if not token:
        print("[instagram_client] No token to refresh — INSTAGRAM_PAGE_TOKEN is not set.")
        return False

    try:
        resp = requests.get(
            _REFRESH_URL,
            params={"grant_type": "ig_refresh_token", "access_token": token},
            timeout=15,
        )
        body = resp.text
        resp.raise_for_status()
        new_token = resp.json().get("access_token")
        expires_in = resp.json().get("expires_in")
    except requests.HTTPError as exc:
        if _MIN_TOKEN_AGE_HINT in body:
            print("[instagram_client] Token too new to refresh — retrying tomorrow.")
        else:
            print(f"[instagram_client] WARNING: token refresh failed: "
                  f"{_redact(exc)} — body: {_redact(body)}")
        return False
    except Exception as exc:
        print(f"[instagram_client] WARNING: token refresh failed: {_redact(exc)}")
        return False

    if not new_token:
        print("[instagram_client] WARNING: refresh returned no access_token.")
        return False

    _write_config(_TOKEN_KEY, new_token)
    _write_config(_SEED_KEY, INSTAGRAM_PAGE_TOKEN or "")
    with _token_lock:
        _token_cache = new_token

    days = round(expires_in / 86400) if expires_in else "?"
    print(f"[instagram_client] Token refreshed — valid for ~{days} more days.")
    return True


def _refresh_loop() -> None:
    while True:
        try:
            refresh_token()
        except Exception as exc:
            print(f"[instagram_client] WARNING: refresh loop error: {_redact(exc)}")
        time.sleep(REFRESH_INTERVAL)


def start_token_refresh_scheduler() -> None:
    """Start the daily token refresh in a background daemon thread.
    Call once during application startup."""
    if not INSTAGRAM_PAGE_TOKEN:
        print("[instagram_client] Token refresh disabled — INSTAGRAM_PAGE_TOKEN is not set.")
        return
    thread = threading.Thread(
        target=_refresh_loop,
        name="instagram-token-refresh",
        daemon=True,
    )
    thread.start()
    print(f"[instagram_client] Daemon thread '{thread.name}' launched.")
