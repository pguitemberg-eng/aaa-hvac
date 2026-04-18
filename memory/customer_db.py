"""
memory/customer_db.py
SQLite memory for leads and customers.
"""

import os
import sqlite3
from datetime import datetime
from typing import Optional

DB_PATH = os.getenv("SQLITE_DB_PATH", "memory/hvac_leads.db")


def _conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_tables():
    conn = _conn()
    conn.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT,
            email TEXT,
            address TEXT,
            service_type TEXT,
            urgency TEXT,
            budget TEXT,
            outcome TEXT DEFAULT 'pending',
            booking_url TEXT,
            booking_confirmed INTEGER DEFAULT 0,
            followup_count INTEGER DEFAULT 0,
            source TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS customers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT,
            phone TEXT UNIQUE,
            email TEXT,
            address TEXT,
            total_jobs INTEGER DEFAULT 1,
            last_service TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS followup_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            lead_id INTEGER,
            followup_num INTEGER,
            channel TEXT,
            tone_used TEXT,
            message TEXT,
            sent_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def save_lead(state: dict):
    ensure_tables()
    conn = _conn()
    conn.execute(
        """INSERT OR IGNORE INTO leads
           (name, phone, email, address, service_type, urgency, budget, source)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            state.get("lead_name"),
            state.get("lead_phone"),
            state.get("lead_email"),
            state.get("lead_address"),
            state.get("lead_service_type"),
            state.get("lead_urgency"),
            state.get("lead_budget"),
            state.get("source", "unknown"),
        ),
    )
    conn.commit()
    conn.close()


def upsert_customer(state: dict):
    ensure_tables()
    conn = _conn()
    phone = state.get("lead_phone", "")
    existing = conn.execute(
        "SELECT id FROM customers WHERE phone = ?", (phone,)
    ).fetchone()
    if existing:
        conn.execute(
            """UPDATE customers SET total_jobs = total_jobs + 1,
               last_service = ? WHERE phone = ?""",
            (state.get("lead_service_type"), phone),
        )
    else:
        conn.execute(
            """INSERT INTO customers (name, phone, email, address, last_service)
               VALUES (?, ?, ?, ?, ?)""",
            (
                state.get("lead_name"),
                phone,
                state.get("lead_email"),
                state.get("lead_address"),
                state.get("lead_service_type"),
            ),
        )
    conn.commit()
    conn.close()


def get_customer_history(phone: str) -> Optional[dict]:
    ensure_tables()
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM customers WHERE phone = ? LIMIT 1", (phone,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def format_customer_context(customer: Optional[dict]) -> str:
    if not customer:
        return "New customer - no previous history."
    return (
        f"Returning customer: {customer.get('name')} | "
        f"Total jobs: {customer.get('total_jobs')} | "
        f"Last service: {customer.get('last_service')}"
    )


def log_followup(state: dict, followup_num: int,
                 message: str, tone_used: str, channel: str):
    ensure_tables()
    conn = _conn()
    conn.execute(
        """INSERT INTO followup_log
           (followup_num, channel, tone_used, message)
           VALUES (?, ?, ?, ?)""",
        (followup_num, channel, tone_used, message),
    )
    conn.commit()
    conn.close()