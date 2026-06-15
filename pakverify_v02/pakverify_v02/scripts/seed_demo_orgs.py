"""
scripts/seed_demo_orgs.py

Creates demo organizations across all three pricing tiers so you can
exercise the billing engine (HTTP 402 paths) and webhook dispatch without
hand-editing the database.

Usage:
    python scripts/seed_demo_orgs.py
"""

import sys
import uuid
from pathlib import Path
from datetime import date

sys.path.insert(0, str(Path(__file__).parent.parent))

from app.core.database import init_db, get_db_connection, TIER_PAYG, TIER_GROWTH, TIER_ENTERPRISE

DEMO_ORGS = [
    {
        "company_name": "Demo Fintech (Pay-As-You-Go)",
        "api_key": "demo-payg-key",
        "pricing_tier": TIER_PAYG,
        "monthly_quota": 0,          # unlimited, billed per verification
        "is_active": 1,
        "webhook_url": None,
        "webhook_secret": None,
    },
    {
        "company_name": "Demo Lender (Growth)",
        "api_key": "demo-growth-key",
        "pricing_tier": TIER_GROWTH,
        "monthly_quota": 0,          # 500 included, unlimited overage at $0.25
        "is_active": 1,
        "webhook_url": "http://127.0.0.1:9000/webhooks/pakverify",
        "webhook_secret": "growth-webhook-secret",
    },
    {
        "company_name": "Demo Bank (Enterprise, near quota)",
        "api_key": "demo-enterprise-key",
        "pricing_tier": TIER_ENTERPRISE,
        "monthly_quota": 1,          # set low so you can trigger the 402 quickly
        "is_active": 1,
        "webhook_url": "http://127.0.0.1:9000/webhooks/pakverify",
        "webhook_secret": "enterprise-webhook-secret",
    },
    {
        "company_name": "Demo Suspended Org",
        "api_key": "demo-suspended-key",
        "pricing_tier": TIER_PAYG,
        "monthly_quota": 0,
        "is_active": 0,             # always 402s at session init
        "webhook_url": None,
        "webhook_secret": None,
    },
]


def main():
    init_db()
    conn = get_db_connection()
    cursor = conn.cursor()

    for org in DEMO_ORGS:
        existing = cursor.execute(
            "SELECT id FROM clients WHERE api_key = ?", (org["api_key"],)
        ).fetchone()
        if existing:
            print(f"Skipping '{org['company_name']}' — API key already exists.")
            continue

        cursor.execute(
            """INSERT INTO clients
               (company_name, api_key, organization_id, is_active, pricing_tier,
                monthly_quota, monthly_usage_counter, billing_cycle_start,
                webhook_url, webhook_secret)
               VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
            (org["company_name"], org["api_key"], f"org_{uuid.uuid4().hex[:12]}",
             org["is_active"], org["pricing_tier"], org["monthly_quota"],
             date.today().isoformat(), org["webhook_url"], org["webhook_secret"])
        )
        print(f"Created '{org['company_name']}' -> X-API-Key: {org['api_key']}")

    conn.commit()
    conn.close()


if __name__ == "__main__":
    main()
