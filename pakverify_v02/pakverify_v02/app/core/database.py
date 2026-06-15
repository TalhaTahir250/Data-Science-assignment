import sqlite3
import logging
import uuid
from pathlib import Path
from datetime import date

logger = logging.getLogger("PakVerify.Database")
DB_PATH = Path(__file__).parent.parent.parent / "pakverify.db"

# Pricing tiers (see Technical Master Brief v0.2, section 3)
TIER_PAYG = "PAY_AS_YOU_GO"
TIER_GROWTH = "GROWTH"
TIER_ENTERPRISE = "ENTERPRISE"
VALID_TIERS = (TIER_PAYG, TIER_GROWTH, TIER_ENTERPRISE)

GROWTH_INCLUDED_VERIFICATIONS = 500


def get_db_connection():
    """Opens a secure connection to the SQLite database."""
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _column_exists(cursor, table: str, column: str) -> bool:
    cursor.execute(f"PRAGMA table_info({table})")
    return any(row[1] == column for row in cursor.fetchall())


def _add_column_if_missing(cursor, table: str, column: str, ddl: str):
    if not _column_exists(cursor, table, column):
        cursor.execute(f"ALTER TABLE {table} ADD COLUMN {ddl}")
        logger.info(f"Migration: added column '{column}' to '{table}'.")


def init_db():
    """Builds/migrates the database tables. Safe to call on every startup."""
    conn = get_db_connection()
    cursor = conn.cursor()

    # ── Table 1: Organizations (formerly "clients") — the multi-tenant billing layer ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_name TEXT NOT NULL,
            api_key TEXT UNIQUE NOT NULL,
            is_active BOOLEAN DEFAULT 1,
            total_scans INTEGER DEFAULT 0
        )
    ''')

    # ── Migration: add v0.2 multi-tenant / billing columns to existing table ──
    _add_column_if_missing(cursor, "clients", "organization_id",
                            "organization_id TEXT")
    _add_column_if_missing(cursor, "clients", "pricing_tier",
                            f"pricing_tier TEXT DEFAULT '{TIER_PAYG}'")
    _add_column_if_missing(cursor, "clients", "monthly_quota",
                            "monthly_quota INTEGER DEFAULT 0")
    _add_column_if_missing(cursor, "clients", "monthly_usage_counter",
                            "monthly_usage_counter INTEGER DEFAULT 0")
    _add_column_if_missing(cursor, "clients", "billing_cycle_start",
                            "billing_cycle_start TEXT")
    _add_column_if_missing(cursor, "clients", "webhook_url",
                            "webhook_url TEXT")
    _add_column_if_missing(cursor, "clients", "webhook_secret",
                            "webhook_secret TEXT")

    # Backfill organization_id / billing_cycle_start for pre-existing rows
    cursor.execute("SELECT id, organization_id, billing_cycle_start FROM clients")
    for row in cursor.fetchall():
        updates = {}
        if not row[1]:
            updates["organization_id"] = f"org_{uuid.uuid4().hex[:12]}"
        if not row[2]:
            updates["billing_cycle_start"] = date.today().isoformat()
        if updates:
            set_clause = ", ".join(f"{k} = ?" for k in updates)
            cursor.execute(
                f"UPDATE clients SET {set_clause} WHERE id = ?",
                (*updates.values(), row[0])
            )

    # ── Table 2: Audit Ledger (legacy single-shot /verify endpoint) ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS scan_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_id INTEGER,
            timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
            cnic_number TEXT,
            status TEXT,
            match_strength TEXT,
            risk_level TEXT,
            session_id TEXT,
            FOREIGN KEY(client_id) REFERENCES clients(id)
        )
    ''')
    _add_column_if_missing(cursor, "scan_logs", "session_id", "session_id TEXT")

    # ── Table 3: Sessions — the v0.2 sequential FSM flow ──
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            session_id TEXT PRIMARY KEY,
            client_id INTEGER NOT NULL,
            state TEXT NOT NULL DEFAULT 'INITIATED',
            extracted_data TEXT,
            biometric_result TEXT,
            cnic_number TEXT,
            created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            updated_at DATETIME DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY(client_id) REFERENCES clients(id)
        )
    ''')

    # Auto-inject a test organization so you can keep testing immediately
    cursor.execute("SELECT * FROM clients WHERE api_key = 'pakverify-v01-key'")
    existing = cursor.fetchone()
    if not existing:
        cursor.execute(
            """INSERT INTO clients
               (company_name, api_key, organization_id, pricing_tier,
                monthly_quota, billing_cycle_start)
               VALUES (?, ?, ?, ?, ?, ?)""",
            ("My First Test Client", "pakverify-v01-key",
             f"org_{uuid.uuid4().hex[:12]}", TIER_PAYG, 0, date.today().isoformat())
        )
        logger.info("Database initialized with default test client (PAY_AS_YOU_GO tier).")

    conn.commit()
    conn.close()
