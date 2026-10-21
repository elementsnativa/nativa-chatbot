"""
bluex.py — Estado de un envío en la API Tracking Pull Corp de Bluexpress.

Se activa solo cuando están todas estas variables de entorno:
  BLUEX_TOKEN_URL       — URL para obtener el token (grant_type=client_credentials)
  BLUEX_TRACKING_URL    — URL base de Tracking Pull Corp; se le agrega /{tracking_number}
  BLUEX_CLIENT_ID, BLUEX_CLIENT_SECRET, BLUEX_API_KEY
Opcionales (se envían como headers si existen):
  BX_CLIENT_ACCOUNT, BX_TOKEN, BX_USERNAME (o BX_USERCODE)

Solo se devuelve el estado del envío: nunca direcciones ni datos del comprador.
"""

import os
import time

import requests

_token: dict = {"value": None, "expires": 0}


def _env(name: str) -> str | None:
    value = os.getenv(name)
    return value.strip() if value else None


def is_configured() -> bool:
    return all(_env(k) for k in (
        "BLUEX_TOKEN_URL", "BLUEX_TRACKING_URL", "BLUEX_CLIENT_ID", "BLUEX_CLIENT_SECRET", "BLUEX_API_KEY",
    ))


def _get_token() -> str:
    if _token["value"] and time.time() < _token["expires"] - 60:
        return _token["value"]
    resp = requests.post(
        _env("BLUEX_TOKEN_URL"),
        data={
            "grant_type": "client_credentials",
            "client_id": _env("BLUEX_CLIENT_ID"),
            "client_secret": _env("BLUEX_CLIENT_SECRET"),
        },
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()
    _token["value"] = data["access_token"]
    _token["expires"] = time.time() + int(data.get("expires_in", 3600))
    return _token["value"]


def _headers() -> dict:
    headers = {
        "Authorization": f"Bearer {_get_token()}",
        "x-api-key": _env("BLUEX_API_KEY"),
        "Accept": "application/json",
    }
    for header, var in (("BX-CLIENT_ACCOUNT", "BX_CLIENT_ACCOUNT"), ("BX-TOKEN", "BX_TOKEN"),
                        ("BX-USERNAME", "BX_USERNAME"), ("BX-USERCODE", "BX_USERCODE")):
        if _env(var):
            headers[header] = _env(var)
    return headers


def get_shipment_status(tracking_number: str) -> dict | None:
    """Último estado del envío, o None si no está configurado o falla la consulta."""
    if not is_configured() or not tracking_number:
        return None
    try:
        url = f"{_env('BLUEX_TRACKING_URL').rstrip('/')}/{tracking_number}"
        resp = requests.get(url, headers=_headers(), timeout=15)
        if resp.status_code in (401, 403):
            # Token revocado o expirado: pedir uno nuevo y reintentar una vez
            _token["value"] = None
            resp = requests.get(url, headers=_headers(), timeout=15)
        resp.raise_for_status()
        data = resp.json()
    except Exception as exc:
        print(f"[bluex] ERROR fetching tracking {tracking_number}: {type(exc).__name__}: {exc}")
        return None

    events = [
        t
        for package in data.get("packages") or []
        for t in package.get("trackings") or []
    ]
    events.sort(key=lambda t: t.get("eventDate") or "")
    result = {"estado_envio": data.get("stateDesc")}
    if events:
        last = events[-1]
        result["ultimo_evento"] = last.get("eventCodeDesc") or last.get("eventTypeDesc")
        result["fecha_ultimo_evento"] = last.get("eventDate")
        result["ubicacion_ultimo_evento"] = last.get("location")
    return {k: v for k, v in result.items() if v}
