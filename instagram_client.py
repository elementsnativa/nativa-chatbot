"""
instagram_client.py — Meta Instagram Messaging API client for Nativa Elements.

Provides:
  - send_text(recipient_id, text)   → POST a plain-text message via the Graph API
  - send_image(recipient_id, url)   → POST an image attachment

Environment variables required:
  INSTAGRAM_PAGE_TOKEN   — Page Access Token with instagram_manage_messages permission
  INSTAGRAM_VERIFY_TOKEN — Webhook verify token (defaults to "nativa2024secure")
"""

import os

import requests
from dotenv import load_dotenv

load_dotenv()

INSTAGRAM_PAGE_TOKEN = os.getenv("INSTAGRAM_PAGE_TOKEN")
VERIFY_TOKEN = os.getenv("INSTAGRAM_VERIFY_TOKEN", "nativa2024secure")
API_VERSION = "v25.0"

_BASE_URL = f"https://graph.instagram.com/{API_VERSION}/me/messages"


def send_text(recipient_id: str, text: str) -> dict:
    """Send a plain-text Instagram DM to *recipient_id* (Instagram PSID)."""
    if not INSTAGRAM_PAGE_TOKEN:
        raise RuntimeError("[instagram_client] INSTAGRAM_PAGE_TOKEN is not set")

    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": text},
        "messaging_type": "RESPONSE",
    }
    params = {"access_token": INSTAGRAM_PAGE_TOKEN}

    try:
        resp = requests.post(_BASE_URL, json=payload, params=params, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        print(f"[instagram_client] Message sent to {recipient_id} — mid={data.get('message_id')}")
        return data
    except requests.HTTPError as exc:
        print(f"[instagram_client] HTTP error sending to {recipient_id}: {exc} — body: {exc.response.text}")
        raise
    except Exception as exc:
        print(f"[instagram_client] Unexpected error sending to {recipient_id}: {exc}")
        raise


def send_image(recipient_id: str, image_url: str) -> dict:
    """Send an image attachment via Instagram DM."""
    if not INSTAGRAM_PAGE_TOKEN:
        raise RuntimeError("[instagram_client] INSTAGRAM_PAGE_TOKEN is not set")

    payload = {
        "recipient": {"id": recipient_id},
        "message": {
            "attachment": {
                "type": "image",
                "payload": {"url": image_url, "is_reusable": True},
            }
        },
        "messaging_type": "RESPONSE",
    }
    params = {"access_token": INSTAGRAM_PAGE_TOKEN}

    try:
        resp = requests.post(_BASE_URL, json=payload, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as exc:
        print(f"[instagram_client] HTTP error sending image to {recipient_id}: {exc} — body: {exc.response.text}")
        raise
    except Exception as exc:
        print(f"[instagram_client] Unexpected error sending image to {recipient_id}: {exc}")
        raise
