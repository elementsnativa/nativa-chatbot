"""
database.py — SQLite database manager for Nativa Elements chatbot.
Tables:
  - abandoned_carts
  - whatsapp_conversations
  - instagram_conversations
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
    print(f"[database] Initializing DB at {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
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
                created_at         REAL NOT NULL,
                status             TEXT NOT NULL DEFAULT 'pending',
                message_sent_at    REAL
            )
            """
        )

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS whatsapp_conversations (
                phone           TEXT PRIMARY KEY,
                history         TEXT NOT NULL DEFAULT '[]',
                updated_at      REAL NOT NULL,
                human_takeover  REAL
            )
            """
        )
        try:
            cur.execute("ALTER TABLE whatsapp_conversations ADD COLUMN human_takeover REAL")
            conn.commit()
        except Exception:
            pass

        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS instagram_conversations (
                psid            TEXT PRIMARY KEY,
                history         TEXT NOT NULL DEFAULT '[]',
                updated_at      REAL NOT NULL,
                human_takeover  REAL
            )
            """
        )
        try:
            cur.execute("ALTER TABLE instagram_conversations ADD COLUMN human_takeover REAL")
            conn.commit()
        except Exception:
            pass

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
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


init_db()
