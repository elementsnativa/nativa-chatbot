"""
crm.py — Puente entre el chatbot y el CRM de servicio al cliente (Supabase).

Cada mensaje que entra o sale por WhatsApp, Instagram o Gmail se registra como
parte de un ticket mediante la función SQL crm_ingest_message. El dashboard web
lee esas tablas en tiempo real.

Routes:
  POST /crm/send                 — el CRM envía una respuesta del agente al cliente
  POST /crm/insights             — genera el resumen semanal de dolores con IA
  POST /webhook/shopify/refund   — registra reembolsos efectivos de Shopify

Environment variables:
  CRM_DATABASE_URL   — cadena de conexión Postgres de Supabase (si falta, el CRM queda apagado)
  CRM_API_SECRET     — secreto compartido con la web del CRM
  CRM_AI_MODEL       — modelo para clasificar tickets (por defecto claude-opus-5)

Mount in main.py with:
    from crm import router as crm_router, start_crm_scheduler
"""

import json
import os
import threading
import time
from datetime import date, datetime, timezone
from typing import Literal, Optional

import anthropic
import psycopg2
import psycopg2.extras
from dotenv import load_dotenv
from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field

load_dotenv()

CRM_DATABASE_URL = (os.getenv("CRM_DATABASE_URL") or "").strip()
CRM_API_SECRET = os.getenv("CRM_API_SECRET", "")
CRM_AI_MODEL = os.getenv("CRM_AI_MODEL", "claude-opus-5")

CLASSIFY_INTERVAL = 120     # segundos entre rondas de clasificación IA
AUTOCLOSE_INTERVAL = 600    # segundos entre cierres automáticos
CLASSIFY_QUIET_SECONDS = 60  # esperar a que el cliente termine de escribir
MAX_INSIGHT_TICKETS = 1500  # tope de tickets por análisis (costo y contexto)

# Prefijo de external_id por canal (el mismo que usan los webhooks, para no duplicar)
EXTERNAL_PREFIX = {"whatsapp": "wa", "instagram": "ig", "email": "gmail"}

router = APIRouter()
_anthropic = anthropic.Anthropic(api_key=os.getenv("ANTHROPIC_API_KEY"))


def enabled() -> bool:
    return bool(CRM_DATABASE_URL)


def _connect():
    return psycopg2.connect(CRM_DATABASE_URL)


# ── Ingesta ───────────────────────────────────────────────────────────────────

def ingest(
    channel: str,
    thread_key: str,
    author: str,
    body: str,
    *,
    external_id: str | None = None,
    sent_at: float | datetime | None = None,
    author_email: str | None = None,
    customer: dict | None = None,
    subject: str | None = None,
    needs_human: bool = False,
    attachments: list | None = None,
) -> int | None:
    """Registra un mensaje en el CRM. Nunca lanza: si falla, el chatbot sigue igual."""
    if not enabled() or not thread_key:
        return None
    if isinstance(sent_at, (int, float)):
        sent_at = datetime.fromtimestamp(sent_at, tz=timezone.utc)
    sent_at = sent_at or datetime.now(timezone.utc)
    try:
        conn = _connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT crm_ingest_message(
                        %s::crm_channel, %s, %s::crm_author, %s, %s, %s, %s,
                        %s::jsonb, %s, %s, %s::jsonb)
                    """,
                    (
                        channel, thread_key, author, body or "", external_id, sent_at,
                        author_email, json.dumps(customer or {}), subject, needs_human,
                        json.dumps(attachments or []),
                    ),
                )
                return cur.fetchone()[0]
        finally:
            conn.close()
    except Exception as exc:
        print(f"[crm] WARNING: could not ingest {channel}/{thread_key}: {exc}")
        return None


def _query_one(sql: str, params: tuple):
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchone()
    finally:
        conn.close()


def thread_exists(channel: str, thread_key: str) -> bool:
    return enabled() and _query_one(
        "SELECT 1 FROM tickets WHERE channel = %s::crm_channel AND thread_key = %s LIMIT 1", (channel, thread_key)
    ) is not None


def existing_external_ids(external_ids: list[str]) -> set[str]:
    """Cuáles de estos ids ya están registrados (una sola consulta)."""
    if not enabled() or not external_ids:
        return set()
    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT external_id FROM messages WHERE external_id = ANY(%s)", (external_ids,))
            return {r[0] for r in cur.fetchall()}
    finally:
        conn.close()


def close_stale(channel: str, older_than_days: int) -> int:
    """Cierra conversaciones importadas sin actividad, con la fecha real del último mensaje."""
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT set_config('crm.actor', 'sistema', true)")
            cur.execute(
                """
                UPDATE tickets SET status = 'cerrado'
                WHERE channel = %s::crm_channel AND status <> 'cerrado'
                  AND last_message_at < now() - make_interval(days => %s)
                RETURNING id
                """,
                (channel, older_than_days),
            )
            ids = [r[0] for r in cur.fetchall()]
            if ids:
                # El trigger usa now(); se reemplaza por el momento real del último mensaje
                cur.execute(
                    """
                    UPDATE tickets SET
                        resolved_at = COALESCE(last_agent_msg_at, last_message_at),
                        closed_at = last_message_at,
                        first_resolved_at = COALESCE(last_agent_msg_at, last_message_at)
                    WHERE id = ANY(%s)
                    """,
                    (ids,),
                )
                cur.execute(
                    """
                    UPDATE ticket_events e SET created_at = COALESCE(t.last_agent_msg_at, t.last_message_at)
                    FROM tickets t
                    WHERE e.ticket_id = t.id AND t.id = ANY(%s) AND e.type = 'status' AND e.to_value = 'cerrado'
                    """,
                    (ids,),
                )
            return len(ids)
    finally:
        conn.close()


def mark_needs_human(channel: str, thread_key: str) -> None:
    """El bot escaló o la conversación está en manos humanas: empieza a correr el SLA."""
    if not enabled() or not thread_key:
        return
    try:
        conn = _connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute("SELECT crm_mark_needs_human(%s::crm_channel, %s)", (channel, thread_key))
        finally:
            conn.close()
    except Exception as exc:
        print(f"[crm] WARNING: could not mark {channel}/{thread_key} as needing a human: {exc}")


# ── Clasificación con IA ──────────────────────────────────────────────────────

Fault = Literal["nativa", "cliente", "courier", "ninguno"]
Resolution = Literal["cambio", "devolucion", "reembolso", "reenvio", "cupon", "informacion", "sin_solucion"]
Status = Literal["nuevo", "abierto", "en_proceso", "pendiente_cliente", "pendiente_courier", "resuelto"]


class TicketClassification(BaseModel):
    category: str = Field(description="slug del motivo, uno de la lista entregada")
    fault: Fault
    resolution: Optional[Resolution] = Field(description="solución acordada o en curso; null si aún no hay")
    order_name: Optional[str] = Field(description="número de pedido mencionado, formato #NTVA1234; null si no aparece")
    sentiment: Literal["positivo", "neutral", "negativo", "muy_negativo"]
    suggested_status: Status
    summary: str = Field(description="una frase en español: qué pide el cliente y en qué quedó")


_CLASSIFY_SYSTEM = """Clasificas tickets de servicio al cliente de Nativa Elements, una marca chilena de ropa \
que vende por Shopify y despacha con Bluexpress.

Motivos posibles (usa el slug exacto):
{categories}

Responsabilidad (fault):
- nativa: error nuestro (producto defectuoso, prenda equivocada, pedido incompleto, retraso en despachar, mala info)
- cliente: el cliente se equivocó o cambió de opinión (talla equivocada, arrepentimiento, dirección mal escrita)
- courier: Bluexpress perdió, dañó o atrasó el envío ya despachado
- ninguno: consultas sin error de nadie

suggested_status: lo que indica la conversación. "resuelto" solo si el cliente ya recibió la solución \
o confirmó que no necesita nada más; "pendiente_cliente" si esperamos datos o respuesta del cliente; \
"pendiente_courier" si depende de Bluexpress; "en_proceso" si Nativa está gestionando algo."""


def _classify_ticket(cur, ticket_id: int, categories: list[dict]) -> None:
    cur.execute(
        "SELECT author, body, sent_at FROM messages WHERE ticket_id=%s ORDER BY sent_at LIMIT 60",
        (ticket_id,),
    )
    rows = cur.fetchall()
    if not rows:
        return
    transcript = "\n".join(
        f"[{r['sent_at']:%d-%m %H:%M}] {r['author'].upper()}: {r['body']}" for r in rows
    )
    cats = "\n".join(f"- {c['slug']}: {c['label']}" for c in categories)
    response = _anthropic.messages.parse(
        model=CRM_AI_MODEL,
        max_tokens=2048,
        output_config={"effort": "low"},
        system=_CLASSIFY_SYSTEM.format(categories=cats),
        messages=[{"role": "user", "content": f"Conversación:\n{transcript}"}],
        output_format=TicketClassification,
    )
    result: TicketClassification | None = response.parsed_output
    if result is None:
        print(f"[crm] Classification of ticket {ticket_id} returned nothing (stop_reason={response.stop_reason}).")
        return
    category = result.category if any(c["slug"] == result.category for c in categories) else "otro"

    cur.execute("SELECT set_config('crm.actor', 'ia', true)")
    cur.execute(
        """
        UPDATE tickets SET
            ai_category = %s, ai_fault = %s, ai_resolution = %s, ai_sentiment = %s,
            ai_suggested_status = %s, ai_summary = %s, ai_classified_at = now(),
            -- la IA solo rellena lo que SAC aún no ha decidido
            category        = CASE WHEN category_source = 'sac' THEN category ELSE %s END,
            category_source = CASE WHEN category_source = 'sac' THEN 'sac' ELSE 'ia' END,
            fault           = COALESCE(fault, %s),
            order_name      = COALESCE(order_name, %s)
        WHERE id = %s
        """,
        (
            category, result.fault, result.resolution, result.sentiment,
            result.suggested_status, result.summary,
            category, result.fault, result.order_name, ticket_id,
        ),
    )


def classify_pending() -> int:
    """Clasifica tickets con mensajes nuevos del cliente desde la última clasificación."""
    if not enabled():
        return 0
    done = 0
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT slug, label FROM categories WHERE active ORDER BY sort")
            categories = cur.fetchall()
            cur.execute(
                """
                SELECT id FROM tickets
                WHERE (status <> 'cerrado' OR ai_classified_at IS NULL)
                  AND NOT handled_by_bot_only
                  AND last_customer_msg_at IS NOT NULL
                  AND last_customer_msg_at < now() - make_interval(secs => %s)
                  AND (ai_classified_at IS NULL OR ai_classified_at < last_message_at)
                ORDER BY (status = 'cerrado'), last_customer_msg_at DESC
                LIMIT 20
                """,
                (CLASSIFY_QUIET_SECONDS,),
            )
            ids = [r["id"] for r in cur.fetchall()]
        conn.commit()
        for ticket_id in ids:
            try:
                with conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
                    _classify_ticket(cur, ticket_id, categories)
                done += 1
            except anthropic.APIError as exc:
                print(f"[crm] Claude error classifying ticket {ticket_id}: {exc}")
            except Exception as exc:
                print(f"[crm] ERROR classifying ticket {ticket_id}: {exc}")
    finally:
        conn.close()
    return done


def fill_instagram_profiles() -> int:
    """Completa nombre y @usuario de clientes de Instagram que entraron sin ellos."""
    if not enabled():
        return 0
    from instagram_client import get_profile

    conn = _connect()
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT id, instagram_psid FROM customers "
                "WHERE instagram_psid IS NOT NULL AND instagram_username IS NULL LIMIT 20"
            )
            pending = cur.fetchall()
        filled = 0
        for customer_id, psid in pending:
            profile = get_profile(psid)
            if not profile.get("username"):
                continue
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE customers SET instagram_username = %s, name = COALESCE(name, %s) WHERE id = %s",
                    (profile["username"], profile.get("name"), customer_id),
                )
            filled += 1
        return filled
    finally:
        conn.close()


def autoclose() -> None:
    if not enabled():
        return
    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute("SELECT crm_autoclose()")
    finally:
        conn.close()


def start_crm_scheduler() -> None:
    if not enabled():
        print("[crm] CRM_DATABASE_URL not set — CRM integration disabled.")
        return
    from crm_gmail import poll_gmail, gmail_enabled, POLL_INTERVAL

    def loop():
        last = {"gmail": 0.0, "classify": 0.0, "autoclose": 0.0, "profiles": 0.0}
        while True:
            now = time.time()
            jobs = [
                ("gmail", POLL_INTERVAL, poll_gmail if gmail_enabled() else None),
                ("classify", CLASSIFY_INTERVAL, classify_pending),
                ("autoclose", AUTOCLOSE_INTERVAL, autoclose),
                ("profiles", AUTOCLOSE_INTERVAL, fill_instagram_profiles),
            ]
            for name, every, fn in jobs:
                if fn and now - last[name] >= every:
                    last[name] = now
                    try:
                        fn()
                    except Exception as exc:
                        print(f"[crm] ERROR in {name} job: {exc}")
            time.sleep(10)

    threading.Thread(target=loop, daemon=True, name="crm-scheduler").start()
    print("[crm] Scheduler started.")


# ── API para la web del CRM ───────────────────────────────────────────────────

def _check_secret(secret: str | None) -> None:
    if not CRM_API_SECRET or secret != CRM_API_SECRET:
        raise HTTPException(status_code=403, detail="Acceso denegado")


class SendRequest(BaseModel):
    ticket_id: int
    body: str
    agent_email: str


@router.post("/crm/send")
def crm_send(req: SendRequest, x_crm_secret: str | None = Header(default=None)):
    """Envía la respuesta escrita en el CRM por el canal del ticket y la registra."""
    _check_secret(x_crm_secret)
    text = req.body.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Mensaje vacío")

    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT channel, thread_key, subject FROM tickets WHERE id=%s", (req.ticket_id,))
            t = cur.fetchone()
    finally:
        conn.close()
    if not t:
        raise HTTPException(status_code=404, detail="Ticket no existe")

    external_id = None
    try:
        if t["channel"] == "whatsapp":
            from whatsapp_client import send_text as wa_send
            from whatsapp_routes import _set_whatsapp_takeover
            data = wa_send(t["thread_key"], text)
            external_id = (data.get("messages") or [{}])[0].get("id")
            _set_whatsapp_takeover(t["thread_key"])
        elif t["channel"] == "instagram":
            from instagram_client import send_text as ig_send
            from instagram_routes import pause_bot_for
            data = ig_send(t["thread_key"], text)
            external_id = data.get("message_id")
            pause_bot_for(t["thread_key"], external_id)
        elif t["channel"] == "email":
            from crm_gmail import send_reply
            external_id = send_reply(t["thread_key"], text)
        else:
            raise HTTPException(status_code=400, detail=f"No se puede responder por {t['channel']}")
    except HTTPException:
        raise
    except Exception as exc:
        print(f"[crm] ERROR sending reply on ticket {req.ticket_id}: {exc}")
        raise HTTPException(status_code=502, detail=f"No se pudo enviar: {exc}")

    external_id = external_id and f"{EXTERNAL_PREFIX[t['channel']]}:{external_id}"
    agent = req.agent_email.lower()
    ingest(t["channel"], t["thread_key"], "agente", text, external_id=external_id, author_email=agent)
    if external_id:
        # Si el eco de Meta / Gmail llegó antes, el mensaje ya existe sin autor: se lo asignamos.
        conn = _connect()
        try:
            with conn, conn.cursor() as cur:
                cur.execute(
                    "UPDATE messages SET author_email=%s WHERE external_id=%s AND author_email IS DISTINCT FROM %s",
                    (agent, external_id, agent),
                )
        finally:
            conn.close()
    return {"status": "sent"}


class InsightRequest(BaseModel):
    start: date
    end: date
    requested_by: str


_INSIGHTS_SYSTEM = """Eres analista de experiencia de cliente de Nativa Elements (ropa, Chile). \
Recibes los tickets de servicio al cliente de un período. Escribe en español, en markdown breve:

1. **Mayores dolores**: los 3–5 problemas que más se repiten, con cantidad y ejemplos concretos.
2. **Errores nuestros**: qué está fallando en Nativa (producto, bodega, despacho, información en la web) \
y qué acción concreta lo reduciría.
3. **SLA**: cómo le fue al equipo con la meta de respuesta y resolución, y dónde se pierde el tiempo.
4. **Oportunidades**: cambios en la web, fichas de producto, guía de tallas o el chatbot que evitarían tickets.

Usa solo lo que está en los datos. Sé específico y accionable; nada de relleno."""


@router.post("/crm/insights")
def crm_insights(req: InsightRequest, x_crm_secret: str | None = Header(default=None)):
    _check_secret(x_crm_secret)
    start, end = req.start, req.end
    if end < start or (end - start).days > 400:
        raise HTTPException(status_code=400, detail="Rango de fechas inválido")
    conn = _connect()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                """
                SELECT m.id, m.channel, m.status, m.category, m.fault, m.resolution, m.refund_amount,
                       m.first_response_minutes, m.resolution_minutes, m.reopen_count,
                       m.sla_first_response, m.sla_resolution, t.ai_summary, t.ai_sentiment
                FROM ticket_metrics m JOIN tickets t ON t.id = m.id
                WHERE m.created_at >= (%s::date)::timestamp AT TIME ZONE 'America/Santiago'
                  AND m.created_at <  (%s::date + 1)::timestamp AT TIME ZONE 'America/Santiago'
                  AND NOT m.handled_by_bot_only
                ORDER BY m.created_at
                """,
                (start, end),
            )
            rows = cur.fetchall()
        if not rows:
            return {"status": "empty"}
        note = ""
        if len(rows) > MAX_INSIGHT_TICKETS:
            note = f"(Se muestran los {MAX_INSIGHT_TICKETS} tickets más recientes de {len(rows)}.)\n"
            rows = rows[-MAX_INSIGHT_TICKETS:]
        data = note + json.dumps(rows, default=str, ensure_ascii=False)
        response = _anthropic.messages.create(
            model=CRM_AI_MODEL,
            max_tokens=4096,
            system=_INSIGHTS_SYSTEM,
            messages=[{"role": "user", "content": f"Tickets del {start} al {end}:\n{data}"}],
        )
        if response.stop_reason == "refusal":
            raise HTTPException(status_code=502, detail="El modelo no generó el resumen")
        body = "".join(b.text for b in response.content if b.type == "text").strip()
        with conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO insights (period_start, period_end, body, created_by) VALUES (%s, %s, %s, %s)",
                (start, end, body, req.requested_by.lower()),
            )
    finally:
        conn.close()
    return {"status": "ok"}


# ── Reembolsos efectivos desde Shopify ───────────────────────────────────────

@router.post("/webhook/shopify/refund")
async def shopify_refund(request: Request):
    from whatsapp_routes import SHOPIFY_WEBHOOK_SECRET, _verify_shopify_hmac

    body_bytes = await request.body()
    if SHOPIFY_WEBHOOK_SECRET:
        signature = request.headers.get("X-Shopify-Hmac-Sha256", "")
        if not _verify_shopify_hmac(SHOPIFY_WEBHOOK_SECRET, body_bytes, signature):
            raise HTTPException(status_code=401, detail="Invalid HMAC signature")
    if not enabled():
        return {"status": "ok"}

    refund = json.loads(body_bytes)
    amount = sum(float(tx.get("amount") or 0) for tx in refund.get("transactions") or []
                 if tx.get("kind") == "refund" and tx.get("status") == "success")
    order_name = None
    try:
        from shopify_tools import API_VERSION, STORE_URL, _headers
        import requests
        resp = requests.get(
            f"https://{STORE_URL}/admin/api/{API_VERSION}/orders/{refund['order_id']}.json",
            headers=_headers(), params={"fields": "name"}, timeout=15,
        )
        order_name = resp.json().get("order", {}).get("name")
    except Exception as exc:
        print(f"[crm] WARNING: could not fetch order name for refund {refund.get('id')}: {exc}")

    conn = _connect()
    try:
        with conn, conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO shopify_refunds (id, order_name, amount, note, created_at)
                VALUES (%s, %s, %s, %s, %s) ON CONFLICT (id) DO NOTHING
                """,
                (str(refund["id"]), order_name, round(amount), refund.get("note"), refund.get("created_at")),
            )
    finally:
        conn.close()
    return {"status": "ok"}
