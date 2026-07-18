"""
dashboard_routes.py — API endpoints for the Nativa admin dashboard.

Endpoints (all require ?secret=ADMIN_SECRET):
  GET  /dashboard                     — Serve the dashboard HTML
  GET  /api/dashboard/knowledge       — Read conocimiento.txt
  POST /api/dashboard/knowledge       — Save conocimiento.txt
  GET  /api/dashboard/prompt          — Read system prompt sections
  GET  /api/dashboard/flows           — List Instagram automation flows
  POST /api/dashboard/flows           — Create flow
  PUT  /api/dashboard/flows/{id}      — Update flow
  DELETE /api/dashboard/flows/{id}    — Delete flow
  GET  /api/dashboard/cart-stats      — Cart recovery statistics
  GET  /api/dashboard/cart-config     — Cart template configuration
  PUT  /api/dashboard/cart-config     — Save cart template configuration
"""

import os
import time

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from database import get_db

router = APIRouter()
ADMIN_SECRET = os.getenv("ADMIN_SECRET", "nativa-admin-2024")
KNOWLEDGE_PATH = os.path.join(os.path.dirname(__file__), "conocimiento.txt")


def _auth(secret: str):
    if secret != ADMIN_SECRET:
        raise HTTPException(status_code=403, detail="Acceso denegado")


# ── Dashboard HTML ────────────────────────────────────────────────────────────

@router.get("/dashboard", response_class=HTMLResponse)
def dashboard_page(secret: str = ""):
    if secret != ADMIN_SECRET:
        return HTMLResponse("<h3>Acceso denegado</h3>", status_code=403)
    html_path = os.path.join(os.path.dirname(__file__), "dashboard.html")
    with open(html_path, encoding="utf-8") as f:
        return HTMLResponse(f.read())


# ── Knowledge ─────────────────────────────────────────────────────────────────

@router.get("/api/dashboard/knowledge")
def get_knowledge(secret: str = ""):
    _auth(secret)
    try:
        with open(KNOWLEDGE_PATH, encoding="utf-8") as f:
            return {"content": f.read()}
    except FileNotFoundError:
        return {"content": ""}


class KnowledgeBody(BaseModel):
    content: str


@router.post("/api/dashboard/knowledge")
def save_knowledge(body: KnowledgeBody, secret: str = ""):
    _auth(secret)
    with open(KNOWLEDGE_PATH, "w", encoding="utf-8") as f:
        f.write(body.content)
    return {"status": "ok"}


# ── System Prompt ─────────────────────────────────────────────────────────────

@router.get("/api/dashboard/prompt")
def get_prompt(secret: str = ""):
    _auth(secret)
    from prompts import SYSTEM_PROMPT
    sections = []
    current_title = "General"
    current_lines: list[str] = []
    for line in SYSTEM_PROMPT.split("\n"):
        if line.startswith("━━"):
            if current_lines:
                sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
            current_title = line.replace("━━", "").strip()
            current_lines = []
        else:
            current_lines.append(line)
    if current_lines:
        sections.append({"title": current_title, "content": "\n".join(current_lines).strip()})
    return {"sections": sections}


# ── Instagram Flows ───────────────────────────────────────────────────────────

class FlowBody(BaseModel):
    name: str
    trigger_type: str
    trigger_value: str = "*"
    message: str
    active: bool = True


@router.get("/api/dashboard/flows")
def list_flows(secret: str = ""):
    _auth(secret)
    db = get_db()
    try:
        rows = db.execute(
            "SELECT id, name, trigger_type, trigger_value, message, active, created_at FROM ig_flows ORDER BY created_at DESC"
        ).fetchall()
        return {"flows": [dict(r) for r in rows]}
    finally:
        db.close()


@router.post("/api/dashboard/flows")
def create_flow(body: FlowBody, secret: str = ""):
    _auth(secret)
    now = time.time()
    db = get_db()
    try:
        cur = db.execute(
            "INSERT INTO ig_flows (name, trigger_type, trigger_value, message, active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id",
            (body.name, body.trigger_type, body.trigger_value, body.message, body.active, now, now),
        )
        row = cur.fetchone()
        db.commit()
        return {"status": "ok", "id": row["id"]}
    finally:
        db.close()


@router.put("/api/dashboard/flows/{flow_id}")
def update_flow(flow_id: int, body: FlowBody, secret: str = ""):
    _auth(secret)
    db = get_db()
    try:
        db.execute(
            "UPDATE ig_flows SET name=?, trigger_type=?, trigger_value=?, message=?, active=?, updated_at=? WHERE id=?",
            (body.name, body.trigger_type, body.trigger_value, body.message, body.active, time.time(), flow_id),
        )
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


@router.delete("/api/dashboard/flows/{flow_id}")
def delete_flow(flow_id: int, secret: str = ""):
    _auth(secret)
    db = get_db()
    try:
        db.execute("DELETE FROM ig_flows WHERE id=?", (flow_id,))
        db.commit()
        return {"status": "ok"}
    finally:
        db.close()


# ── Cart Recovery Stats ───────────────────────────────────────────────────────

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


# ── Cart Config ───────────────────────────────────────────────────────────────

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
