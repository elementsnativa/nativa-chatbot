"""
database.py — SQLite database manager for Nativa Elements chatbot.
Tables:
  - abandoned_carts
  - whatsapp_conversations
  - completed_orders

DB_PATH defaults to /tmp/nativa_chatbot.db (Railway-compatible).
init_db() is called automatically at module import.
"""

import os
import sqlite3

from dotenv import load_dotenv

load_dotenv()

DB_PATH = os.getenv("DB_PATH", "/tmp/nativa_chatbot.db")


def init_db() -> None:
    """Create all tables if they don't exist yet."""
    print(f"[database] Initializing DB at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    try:
        cur = conn.cursor()

        # ── abandoned_carts ──────────────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS abandoned_carts (
                token              TEXT PRIMARY KEY,
                phone              TEXT,
                name               TEXT,
                products           TEXT,          -- JSON list of {title, price}
                checkout_url       TEXT,
                total              TEXT,
                created_at         REAL NOT NULL,
                status             TEXT NOT NULL DEFAULT 'pending',
                                                  -- pending | sent | converted
                                                  -- error   | no_phone
                message_sent_at    REAL
            )
            """
        )

        # ── whatsapp_conversations ───────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_conversations (
                phone           TEXT PRIMARY KEY,
                history         TEXT NOT NULL DEFAULT '[]',  -- JSON list of messages
                updated_at      REAL NOT NULL,
                human_takeover  REAL  -- timestamp of last human reply; NULL = bot active
            )
            """
        )
        # Migration: add human_takeover if table already exists without it
        try:
            cur.execute("ALTER TABLE whatsapp_conversations ADD COLUMN human_takeover REAL")
            conn.commit()
        except Exception:
            pass  # Column already exists

        # ── instagram_conversations ──────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS instagram_conversations (
                psid            TEXT PRIMARY KEY,
                history         TEXT NOT NULL DEFAULT '[]',  -- JSON list of messages
                updated_at      REAL NOT NULL,
                human_takeover  REAL  -- timestamp of last human reply; NULL = bot active
            )
            """
        )
        # Migration: add human_takeover if table already exists without it
        try:
            cur.execute("ALTER TABLE instagram_conversations ADD COLUMN human_takeover REAL")
            conn.commit()
        except Exception:
            pass  # Column already exists

        # ── completed_orders ─────────────────────────────────────────────────
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS completed_orders (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                email         TEXT,
                phone         TEXT,
                completed_at  REAL NOT NULL
            )
            """
        )

        conn.commit()
        print("[database] Tables ready.")
    except Exception as exc:
        print(f"[database] ERROR during init_db: {exc}")
        raise
    finally:
        conn.close()


def get_db() -> sqlite3.Connection:
    """Return an open sqlite3.Connection with row_factory set to Row."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


# ── Auto-initialise when the module is imported ──────────────────────────────
init_db()
