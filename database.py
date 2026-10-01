"""
database.py — PostgreSQL database manager for Nativa Elements chatbot.
Tables:
  - abandoned_carts
  - whatsapp_conversations
  - instagram_conversations
  - completed_orders
  - recovery_sends
  - recovery_replies

Uses DATABASE_URL env var (postgresql://...).
DBWrapper mimics sqlite3's connection interface so routes need no changes.
"""

import os

import psycopg2
import psycopg2.extras
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")


class DBWrapper:
    """Wraps a psycopg2 connection to match the sqlite3 interface used in routes."""

    def __init__(self, conn):
        self._conn = conn
        self._cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)

    def execute(self, query: str, params=None):
        query = query.replace("?", "%s")
        self._cur.execute(query, params)
        return self._cur

    def commit(self):
        self._conn.commit()

    def rollback(self):
        """Clear an aborted transaction. PostgreSQL refuses every further
        statement on a connection whose last statement failed."""
        self._conn.rollback()

    def close(self):
        self._cur.close()
        self._conn.close()


def _connect() -> psycopg2.extensions.connection:
    return psycopg2.connect(DATABASE_URL.strip())


def init_db() -> None:
    print("[database] Initializing PostgreSQL DB")
    conn = _connect()
    try:
        cur = conn.cursor()

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS abandoned_carts (
                token              TEXT PRIMARY KEY,
                phone              TEXT,
                name               TEXT,
                products           TEXT,
                checkout_url       TEXT,
                total              TEXT,
                created_at         DOUBLE PRECISION NOT NULL,
                status             TEXT NOT NULL DEFAULT 'pending',
                message_sent_at    DOUBLE PRECISION
            )
            """
        )

        # Columns added after abandoned_carts first shipped.
        #   stage             — how many recovery messages this cart has received
        #   last_sent_at      — when the most recent one went out
        #   accepts_marketing — Meta requires opt-in for Marketing templates
        for column, ddl in (
            ("stage", "INTEGER NOT NULL DEFAULT 0"),
            ("last_sent_at", "DOUBLE PRECISION"),
            ("accepts_marketing", "BOOLEAN NOT NULL DEFAULT FALSE"),
        ):
            cur.execute(f"ALTER TABLE abandoned_carts ADD COLUMN IF NOT EXISTS {column} {ddl}")

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_carts_status ON abandoned_carts (status, created_at)"
        )

        # One row per recovery message handed to the Cloud API. Drives the
        # per-phone cooldown and makes the funnel measurable.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_sends (
                id           BIGSERIAL PRIMARY KEY,
                phone        TEXT NOT NULL,
                cart_token   TEXT NOT NULL,
                stage        INTEGER NOT NULL,
                template     TEXT NOT NULL,
                message_id   TEXT,
                sent_at      DOUBLE PRECISION NOT NULL
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_sends_phone ON recovery_sends (phone, sent_at)"
        )

        # Delivery lifecycle, filled in from the WhatsApp status webhook, plus
        # the click, which only exists when the button points at our redirect.
        for column, ddl in (
            ("delivered_at", "DOUBLE PRECISION"),
            ("read_at", "DOUBLE PRECISION"),
            ("failed_at", "DOUBLE PRECISION"),
            ("error_code", "TEXT"),
            ("clicked_at", "DOUBLE PRECISION"),
            ("click_count", "INTEGER NOT NULL DEFAULT 0"),
        ):
            cur.execute(f"ALTER TABLE recovery_sends ADD COLUMN IF NOT EXISTS {column} {ddl}")

        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_sends_msgid ON recovery_sends (message_id)"
        )

        # What customers write back after a recovery message. Their replies are
        # the only qualitative read on whether the campaign lands.
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS recovery_replies (
                id              BIGSERIAL PRIMARY KEY,
                phone           TEXT NOT NULL,
                cart_token      TEXT,
                stage           INTEGER,
                template        TEXT,
                body            TEXT NOT NULL,
                replied_at      DOUBLE PRECISION NOT NULL,
                latency_seconds DOUBLE PRECISION
            )
            """
        )
        cur.execute(
            "CREATE INDEX IF NOT EXISTS idx_recovery_replies_at ON recovery_replies (replied_at)"
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_conversations (
                phone           TEXT PRIMARY KEY,
                history         TEXT NOT NULL DEFAULT '[]',
                updated_at      DOUBLE PRECISION NOT NULL,
                human_takeover  DOUBLE PRECISION
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS instagram_conversations (
                psid            TEXT PRIMARY KEY,
                history         TEXT NOT NULL DEFAULT '[]',
                updated_at      DOUBLE PRECISION NOT NULL,
                human_takeover  DOUBLE PRECISION
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS completed_orders (
                id            BIGSERIAL PRIMARY KEY,
                email         TEXT,
                phone         TEXT,
                completed_at  DOUBLE PRECISION NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS ig_flows (
                id           BIGSERIAL PRIMARY KEY,
                name         TEXT NOT NULL,
                trigger_type TEXT NOT NULL,
                trigger_value TEXT NOT NULL DEFAULT '*',
                message      TEXT NOT NULL,
                active       BOOLEAN NOT NULL DEFAULT TRUE,
                created_at   DOUBLE PRECISION NOT NULL,
                updated_at   DOUBLE PRECISION NOT NULL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_config (
                key        TEXT PRIMARY KEY,
                value      TEXT NOT NULL,
                updated_at DOUBLE PRECISION NOT NULL
            )
            """
        )

        # Default cart recovery templates
        cur.execute(
            """
            INSERT INTO bot_config (key, value, updated_at)
            VALUES
                ('cart_template_first',     'msj_1',                %s),
                ('cart_template_returning', 'antiguo_con_codigo',   %s),
                ('cart_template_followup',  'cliente_nuevo2_',      %s),
                ('cart_stage1_template',    'carrito_abandonado',   %s),
                ('cart_stage2_template',    'carrito_24h',          %s),
                ('cart_stage3_template',    'carrito_72h',          %s),
                ('cart_template_lang',      'es',                   %s)
            ON CONFLICT (key) DO NOTHING
            """,
            (0.0,) * 7,
        )

        conn.commit()
        print("[database] Tables ready.")
    except Exception as exc:
        print(f"[database] ERROR during init_db: {exc}")
        raise
    finally:
        cur.close()
        conn.close()


def get_db() -> DBWrapper:
    return DBWrapper(_connect())


init_db()
