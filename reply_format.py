"""Formato de la respuesta del bot: un solo mensaje, con párrafos separados por líneas en blanco."""

import re


def split_reply(text: str) -> list[str]:
    """Devuelve la respuesta como UN solo mensaje; colapsa saltos de línea extra entre párrafos."""
    text = re.sub(r"\n\s*\n+", "\n\n", (text or "").strip())
    return [text] if text else []


def typing_seconds(part: str) -> float:
    """Pausa de "escribiendo" según el largo del mensaje: entre 1.5 y 3 segundos."""
    return min(3.0, 1.5 + len(part) / 120)
