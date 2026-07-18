"""Carritos tab — cart recovery stats + template config."""

import time

from fastapi import APIRouter
from pydantic import BaseModel

from ._shared import _auth
from database import get_db

router = APIRouter()


@router.get("/api/dashboard/cart-stats")
def cart_stats(secret: str = ""):
    _auth(secret)
    db = get_db()
    try:
        rows = db.execute(
            "SELECT status, COUNT(*) as count FROM abandoned_carts GROUP BY status"
        ).fetchall()
        stats = {r["status"]: r["count"] for r in rows}
        return {
            "pending":   stats.get("pending", 0),
            "sent":      stats.get("sent", 0) + stats.get("sent_followup_pending", 0),
            "converted": stats.get("converted", 0),
            "error":     stats.get("error", 0),
            "skipped":   stats.get("skipped", 0),
            "no_phone":  stats.get("no_phone", 0),
            "total":     sum(stats.values()),
        }
    finally:
        db.close()


@router.get("/api/dashboard/cart-config")
def get_cart_config(secret: str = ""):
    _auth(secret)
    db = get_db()
    try:
        rows = db.execute(
            "SELECT key, value FROM bot_config WHERE key LIKE 'cart_template_%'"
        ).fetchall()
        return {r["key"]: r["value"] for r in rows}
    finally:
        db.close()


class CartConfigBody(BaseModel):
    cart_template_first: str
    cart_template_returning: str
    cart_template_followup: str


@router.put("/api/dashboard/cart-config")
def save_cart_config(body: CartConfigBody, secret: str = ""):
    _auth(secret)
    now = time.time()
    db = get_db()
    try:
        for key, value in body.model_dump().items():
            db.execute(
                "INSERT INTO bot_config (key, value, updated_at) VALUES (?, ?, ?) ON CONFLICT (key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                (key, value, now),
            )
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()
