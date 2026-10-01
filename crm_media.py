"""
crm_media.py — Guarda en Supabase Storage las fotos y archivos que mandan los clientes.

WhatsApp e Instagram entregan links temporales (Meta borra los archivos con el
tiempo), así que se descargan al llegar y quedan en el bucket privado
"attachments". Gmail no lo necesita: el CRM pide el adjunto a Gmail al abrirlo.

Environment variables:
  SUPABASE_URL         — https://<proyecto>.supabase.co
  SUPABASE_SECRET_KEY  — clave secreta (sb_secret_…) para subir archivos
"""

import mimetypes
import os
import re

import requests
from dotenv import load_dotenv

load_dotenv()

SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")
BUCKET = "attachments"
MAX_BYTES = 25 * 1024 * 1024
WA_API = "https://graph.facebook.com/v21.0"

_WA_LABEL = {"image": "imagen", "audio": "audio", "video": "video", "document": "documento", "sticker": "sticker"}


def enabled() -> bool:
    return bool(SUPABASE_URL and SUPABASE_SECRET_KEY)


def _headers() -> dict:
    headers = {"apikey": SUPABASE_SECRET_KEY}
    if SUPABASE_SECRET_KEY.startswith("eyJ"):  # clave service_role antigua (JWT)
        headers["Authorization"] = f"Bearer {SUPABASE_SECRET_KEY}"
    return headers


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", name)[:120] or "archivo"


def store(data: bytes, path: str, mime: str) -> str | None:
    """Sube el archivo al bucket. Devuelve la ruta guardada o None si falla."""
    if not enabled() or not data or len(data) > MAX_BYTES:
        return None
    try:
        resp = requests.post(
            f"{SUPABASE_URL}/storage/v1/object/{BUCKET}/{path}",
            headers={**_headers(), "Content-Type": mime or "application/octet-stream", "x-upsert": "true"},
            data=data,
            timeout=30,
        )
        resp.raise_for_status()
        return path
    except Exception as exc:
        print(f"[crm_media] WARNING: could not store {path}: {exc}")
        return None


def _download(url: str, headers: dict | None = None) -> tuple[bytes, str] | None:
    try:
        resp = requests.get(url, headers=headers or {}, timeout=30)
        resp.raise_for_status()
        return resp.content, resp.headers.get("Content-Type", "").split(";")[0]
    except Exception as exc:
        print(f"[crm_media] WARNING: could not download media: {exc}")
        return None


def whatsapp_attachments(message: dict) -> list[dict]:
    """Descarga la foto / archivo de un mensaje de WhatsApp y lo guarda. [] si no trae."""
    kind = message.get("type")
    media = message.get(kind) if isinstance(message.get(kind), dict) else None
    if kind not in _WA_LABEL or not media or not media.get("id"):
        return []
    item = {"type": kind, "mime": media.get("mime_type"), "name": media.get("filename") or _WA_LABEL[kind]}
    if not enabled():
        return [item]
    token = os.getenv("WHATSAPP_TOKEN", "")
    auth = {"Authorization": f"Bearer {token}"}
    try:
        info = requests.get(f"{WA_API}/{media['id']}", headers=auth, timeout=15).json()
    except Exception as exc:
        print(f"[crm_media] WARNING: could not resolve WhatsApp media {media['id']}: {exc}")
        return [item]
    got = _download(info.get("url", ""), auth) if info.get("url") else None
    if got:
        data, mime = got
        mime = media.get("mime_type") or mime
        ext = mimetypes.guess_extension(mime or "") or ""
        name = media.get("filename") or f"{kind}{ext}"
        item.update(mime=mime, name=name, size=len(data),
                    path=store(data, f"whatsapp/{_safe(message.get('id', media['id']))}/{_safe(name)}", mime))
    return [item]


def instagram_attachments(message: dict) -> list[dict]:
    """Guarda las fotos / videos de un DM de Instagram (sus links expiran)."""
    out = []
    for n, att in enumerate(message.get("attachments") or []):
        kind = att.get("type", "file")
        url = (att.get("payload") or {}).get("url")
        item = {"type": kind, "name": kind}
        if url and enabled() and kind in ("image", "video", "audio", "file"):
            got = _download(url)
            if got:
                data, mime = got
                name = f"{kind}{n}{mimetypes.guess_extension(mime or '') or ''}"
                item.update(mime=mime, name=name, size=len(data),
                            path=store(data, f"instagram/{_safe(message.get('mid', 'x'))}/{name}", mime))
        if not item.get("path") and url:
            item["url"] = url  # al menos el link, mientras dure
        out.append(item)
    return out
