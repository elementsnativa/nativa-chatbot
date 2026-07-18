import os

from fastapi import HTTPException

ADMIN_SECRET = os.getenv("ADMIN_SECRET", "nativa-admin-2024")


def _auth(secret: str) -> None:
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Acceso denegado")
