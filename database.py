"""
database.py — PostgreSQL database manager for Nativa Elements chatbot.
Tables:
  - abandoned_carts
  - whatsapp_conversations
  - instagram_conversations
  - completed_orders

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
