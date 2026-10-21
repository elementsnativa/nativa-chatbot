"""Separa la respuesta del modelo en 2-3 mensajes cortos, estilo chat."""

import re

MAX_PARTS = 3


def split_reply(text: str) -> list[str]:
    """Divide por líneas en blanco; si salen más de MAX_PARTS, junta el resto en el último."""
    parts = [p.strip() for p in re.split(r"\n\s*\n", text or "") if p.strip()]
    if len(parts) > MAX_PARTS:
        parts = parts[:MAX_PARTS - 1] + ["\n".join(parts[MAX_PARTS - 1:])]
    return parts or [text.strip()]


def typing_seconds(part: str) -> float:
    """Pausa de "escribiendo" según el largo del mensaje: entre 1.5 y 3 segundos."""
    return min(3.0, 1.5 + len(part) / 120)
