"""
crm_gmail.py — Conecta la casilla sac@nativaelements.com (Google Workspace) al CRM.

Lee la bandeja y los enviados cada minuto con la Gmail API usando una cuenta de
servicio con delegación de dominio, así que no hay que iniciar sesión a mano.
Los correos de clientes entran como mensajes 'cliente'; los que SAC envía desde
Gmail (o desde el CRM) entran como 'agente', así el SLA se mide igual.

Environment variables:
  GMAIL_SERVICE_ACCOUNT_JSON — JSON de la cuenta de servicio (contenido completo)
  GMAIL_SAC_ADDRESS          — casilla a leer (por defecto sac@nativaelements.com)
"""

import base64
import json
import os
import re
from datetime import datetime, timezone
from email.message import EmailMessage
from email.utils import parseaddr

import requests
from dotenv import load_dotenv

import crm

load_dotenv()

SAC_ADDRESS = os.getenv("GMAIL_SAC_ADDRESS", "sac@nativaelements.com").lower()
POLL_INTERVAL = 60
_API = "https://gmail.googleapis.com/gmail/v1/users/me"
_SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
# Remitentes automáticos que no son clientes
_IGNORE_SENDERS = re.compile(
    r"(no-?reply|mailer-daemon|notifications?@|shopify\.com|bluex|facebookmail|google\.com|instagram\.com)",
    re.I,
)

_credentials = None


def gmail_enabled() -> bool:
    return bool(os.getenv("GMAIL_SERVICE_ACCOUNT_JSON"))


def _token() -> str:
    global _credentials
    from google.auth.transport.requests import Request as GoogleRequest
    from google.oauth2 import service_account

    if _credentials is None:
        info = json.loads(os.environ["GMAIL_SERVICE_ACCOUNT_JSON"])
        _credentials = service_account.Credentials.from_service_account_info(
            info, scopes=_SCOPES, subject=SAC_ADDRESS
        )
    if not _credentials.valid:
        _credentials.refresh(GoogleRequest())
    return _credentials.token


def _get(path: str, **params) -> dict:
    resp = requests.get(f"{_API}/{path}", headers={"Authorization": f"Bearer {_token()}"},
                        params=params, timeout=20)
    resp.raise_for_status()
    return resp.json()


def _header(msg: dict, name: str) -> str:
    for h in msg.get("payload", {}).get("headers", []):
        if h["name"].lower() == name.lower():
            return h["value"]
    return ""


def _plain_text(payload: dict) -> str:
    """Texto plano del correo (o el HTML sin etiquetas si no hay texto plano)."""
    def walk(part):
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if mime == "text/plain" and data:
            yield "plain", base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        elif mime == "text/html" and data:
            yield "html", base64.urlsafe_b64decode(data).decode("utf-8", "replace")
        for sub in part.get("parts", []) or []:
            yield from walk(sub)

    parts = list(walk(payload))
    text = next((t for kind, t in parts if kind == "plain"), None)
    if text is None:
        html = next((t for kind, t in parts if kind == "html"), "")
        text = re.sub(r"<[^>]+>", " ", re.sub(r"<(style|script)[^>]*>.*?</\1>", "", html, flags=re.S))
    return _strip_quoted(text)


def _strip_quoted(text: str) -> str:
    """Quita el historial citado ("El lun, ... escribió:" y líneas con '>')."""
    lines = []
    for line in text.splitlines():
        if re.match(r"^\s*(El|On)\s.+(escribió|wrote):\s*$", line):
            break
        if line.startswith(">"):
            continue
        lines.append(line)
    return re.sub(r"\n{3,}", "\n\n", "\n".join(lines)).strip()


def _attachments(payload: dict) -> list:
    found = []

    def walk(part):
        if part.get("filename"):
            found.append({"name": part["filename"], "mime": part.get("mimeType")})
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    return found


def poll_gmail() -> int:
    """Trae los correos de los últimos 2 días que aún no estén en el CRM."""
    listing = _get("messages", q="newer_than:2d -in:chats -category:promotions -category:social",
                   includeSpamTrash="false", maxResults=100)
    ingested = 0
    for ref in reversed(listing.get("messages", [])):  # del más antiguo al más nuevo
        msg = _get(f"messages/{ref['id']}", format="full")
        labels = set(msg.get("labelIds", []))
        sender_name, sender = parseaddr(_header(msg, "From"))
        sender = sender.lower()
        sent_at = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
        subject = _header(msg, "Subject")

        if sender == SAC_ADDRESS or "SENT" in labels:
            _, to_addr = parseaddr(_header(msg, "To"))
            author, customer = "agente", {"email": to_addr.lower()}
        else:
            if _IGNORE_SENDERS.search(sender):
                continue
            author, customer = "cliente", {"email": sender, "name": sender_name or None}

        ticket_id = crm.ingest(
            "email", msg["threadId"], author, _plain_text(msg.get("payload", {})),
            external_id=f"gmail:{msg['id']}",
            sent_at=sent_at,
            author_email=SAC_ADDRESS if author == "agente" else None,
            customer=customer,
            subject=subject,
            needs_human=True,  # el correo no lo atiende el bot
            attachments=_attachments(msg.get("payload", {})),
        )
        if ticket_id:
            ingested += 1
    return ingested


def send_reply(thread_id: str, text: str) -> str:
    """Responde en el mismo hilo de Gmail. Devuelve el id del mensaje enviado."""
    thread = _get(f"threads/{thread_id}", format="metadata",
                  metadataHeaders=["From", "To", "Subject", "Message-ID", "References"])
    last_customer = next(
        (m for m in reversed(thread["messages"])
         if parseaddr(_header(m, "From"))[1].lower() != SAC_ADDRESS),
        thread["messages"][-1],
    )
    subject = _header(last_customer, "Subject")
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"
    message_id = _header(last_customer, "Message-ID")

    email = EmailMessage()
    email["From"] = f"Nativa Elements <{SAC_ADDRESS}>"
    email["To"] = _header(last_customer, "From")
    email["Subject"] = subject
    if message_id:
        email["In-Reply-To"] = message_id
        email["References"] = f"{_header(last_customer, 'References')} {message_id}".strip()
    email.set_content(text)

    raw = base64.urlsafe_b64encode(email.as_bytes()).decode()
    resp = requests.post(f"{_API}/messages/send", headers={"Authorization": f"Bearer {_token()}"},
                         json={"raw": raw, "threadId": thread_id}, timeout=20)
    resp.raise_for_status()
    return resp.json()["id"]
