"""Automatizaciones tab — Instagram flow CRUD."""

import time

from fastapi import APIRouter
from pydantic import BaseModel

from dashboard._shared import _auth
from database import get_db

router = APIRouter()


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
