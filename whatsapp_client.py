"""
whatsapp_client.py — Meta WhatsApp Cloud API client for Nativa Elements.

Provides:
  - send_text(to, text)  → POST a plain-text message via the Cloud API
  - normalize_phone(phone) → normalise Chilean numbers to E.164 digits (no +)

Environment variables required:
  WHATSAPP_TOKEN      — permanent / system-user token from Meta
  WHATSAPP_PHONE_ID   — Phone Number ID from the Meta app dashboard
  WHATSAPP_VERIFY_TOKEN (optional) — defaults to "nativa2024secure"
"""

import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────
WHATSAPP_TOKEN = os.getenv("WHATSAPP_TOKEN")
PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_ID")
VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "nativa2024secure")
API_VERSION = "v21.0"

_BASE_URL = f"https://graph.facebook.com/{API_VERSION}/{PHONE_NUMBER_ID}/messages"


# ── Public helpers ────────────────────────────────────────────────────────────

def send_text(to: str, text: str) -> dict:
    """
    Send a plain-text WhatsApp message to *to* (E.164 digits, no +).

    Returns the parsed JSON response from the Cloud API.
    Raises requests.HTTPError on 4xx/5xx responses.
    """
    if not WHATSAPP_TOKEN:
        raise RuntimeError("[whatsapp_client] WHATSAPP_TOKEN is not set")
    if not PHONE_NUMBER_ID:
        raise RuntimeError("[whatsapp_client] WHATSAPP_PHONE_ID is not set")

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "text",
        "text": {"preview_url": False, "body": text},
    }

    try:
        resp = requests.post(_BASE_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        print(f"[whatsapp_client] Message sent to {to} — message_id={data.get('messages', [{}])[0].get('id')}")
        return data
    except requests.HTTPError as exc:
        print(f"[whatsapp_client] HTTP error sending to {to}: {exc} — body: {exc.response.text}")
        raise
    except Exception as exc:
        print(f"[whatsapp_client] Unexpected error sending to {to}: {exc}")
        raise


def send_template(to: str, template_name: str, params: list[str], language: str = "es") -> dict:
    """
    Send an approved WhatsApp template message (required for business-initiated messages).
    params: list of body parameter values in order, e.g. ["Esteban", "• Polera M"]
    """
    if not WHATSAPP_TOKEN:
        raise RuntimeError("[whatsapp_client] WHATSAPP_TOKEN is not set")
    if not PHONE_NUMBER_ID:
        raise RuntimeError("[whatsapp_client] WHATSAPP_PHONE_ID is not set")

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    components = []
    if params:
        components.append({
            "type": "body",
            "parameters": [{"type": "text", "text": p} for p in params],
        })

    payload = {
        "messaging_product": "whatsapp",
        "to": to,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": language},
            "components": components,
        },
    }

    try:
        resp = requests.post(_BASE_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        print(f"[whatsapp_client] Template '{template_name}' sent to {to}")
        return data
    except requests.HTTPError as exc:
        print(f"[whatsapp_client] HTTP error sending template to {to}: {exc} — body: {exc.response.text}")
        raise
    except Exception as exc:
        print(f"[whatsapp_client] Unexpected error sending template to {to}: {exc}")
        raise


def send_image(to: str, image_url: str, caption: str = "") -> dict:
    """Send an image message via WhatsApp."""
    if not WHATSAPP_TOKEN:
        raise RuntimeError("[whatsapp_client] WHATSAPP_TOKEN is not set")
    if not PHONE_NUMBER_ID:
        raise RuntimeError("[whatsapp_client] WHATSAPP_PHONE_ID is not set")

    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json",
    }
    image_payload: dict = {"link": image_url}
    if caption:
        image_payload["caption"] = caption

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": to,
        "type": "image",
        "image": image_payload,
    }

    try:
        resp = requests.post(_BASE_URL, json=payload, headers=headers, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        print(f"[whatsapp_client] HTTP error sending image to {to}: {exc} — body: {exc.response.text}")
        raise
    except Exception as exc:
        print(f"[whatsapp_client] Unexpected error sending image to {to}: {exc}")
        raise


def normalize_phone(phone: str) -> str | None:
    """
    Normalise a Chilean phone number to E.164 digits WITHOUT the leading +.

    Supported input formats:
      +56912345678  →  56912345678
      56912345678   →  56912345678
      09XXXXXXXX    →  569XXXXXXXX   (mobile with leading 0)
      9XXXXXXXX     →  569XXXXXXXX   (mobile without country code)

    Returns None if the number cannot be normalised to a valid Chilean mobile
    or landline format.
    """
    if not phone:
        return None

    # Strip spaces, hyphens, parentheses
    cleaned = re.sub(r"[\s\-\(\)\.]+", "", phone)

    # Remove leading +
    if cleaned.startswith("+"):
        cleaned = cleaned[1:]

    # Already in full E.164 form: 56 + 9 digits (mobile) or 56 + 8-9 digits
    if re.fullmatch(r"56[2-9]\d{7,8}", cleaned):
        return cleaned

    # Chilean mobile starting with 09XXXXXXXX → 569XXXXXXXX
    if re.fullmatch(r"09\d{8}", cleaned):
        return "56" + cleaned[1:]

    # Chilean mobile starting with 9XXXXXXXX (9 digits total) → 569XXXXXXXX
    if re.fullmatch(r"9\d{8}", cleaned):
        return "56" + cleaned

    # Chilean landline 8 digits (e.g. 22XXXXXX) → 5622XXXXXX
    if re.fullmatch(r"[2-9]\d{7}", cleaned):
        return "56" + cleaned

    print(f"[whatsapp_client] Could not normalise phone: '{phone}' (cleaned='{cleaned}')")
    return None
