"""
app/utils/billing.py

B2B Multi-Tenant Billing & Pricing Engine (Technical Master Brief v0.2, section 3).

Pricing tiers
-------------
- PAY_AS_YOU_GO: $0.40 / successful verification, no fixed monthly commitment.
                 monthly_quota = 0 -> effectively unlimited (billed per use).
- GROWTH:        $150/month flat fee covering up to 500 verifications,
                 $0.25 / overage verification beyond that.
                 monthly_quota = 0 -> unlimited overage allowed (billed extra).
                 An org admin MAY set monthly_quota > 0 as a hard spending cap.
- ENTERPRISE:    Custom volume commitment. monthly_quota is the dedicated
                 monthly allowance; once reached, sessions are blocked until
                 the billing cycle resets (or the quota is raised).

Quota enforcement rule (kept deliberately simple, per the brief):
    monthly_quota == 0  -> no hard cap, session always allowed (subject to is_active)
    monthly_quota  > 0  -> session blocked with HTTP 402 once
                           monthly_usage_counter >= monthly_quota

Usage metering rule:
    Only sessions that reach a TERMINAL state (VERIFIED, REJECTED,
    SPOOF_DETECTED) increment monthly_usage_counter. Sessions abandoned
    during document capture (bad image, user timeout, etc.) do NOT count.
"""

import sqlite3
import logging
from datetime import date
from typing import Optional, Tuple

from app.core.database import TIER_GROWTH, TIER_ENTERPRISE, GROWTH_INCLUDED_VERIFICATIONS

logger = logging.getLogger("PakVerify.Billing")

# Terminal states that count as a "processed transaction" for billing purposes.
BILLABLE_TERMINAL_STATES = {"VERIFIED", "REJECTED", "SPOOF_DETECTED"}

# Per-unit pricing, used only for cost-estimate fields in API responses /
# reporting. Actual invoicing is out of scope for this build.
UNIT_PRICE_USD = {
    "PAY_AS_YOU_GO": 0.40,
    "GROWTH_OVERAGE": 0.25,
}


def _reset_billing_cycle_if_needed(cursor: sqlite3.Cursor, client: sqlite3.Row) -> sqlite3.Row:
    """
    If the org's billing_cycle_start is from a previous calendar month,
    reset monthly_usage_counter to 0 and roll billing_cycle_start forward.
    Returns the (possibly refreshed) client row.
    """
    today = date.today()
    cycle_start_raw = client["billing_cycle_start"]

    needs_reset = True
    if cycle_start_raw:
        try:
            cycle_start = date.fromisoformat(cycle_start_raw)
            needs_reset = (cycle_start.year, cycle_start.month) != (today.year, today.month)
        except ValueError:
            needs_reset = True

    if needs_reset:
        cursor.execute(
            "UPDATE clients SET monthly_usage_counter = 0, billing_cycle_start = ? WHERE id = ?",
            (today.isoformat(), client["id"])
        )
        logger.info(f"Billing cycle reset for org '{client['organization_id']}'.")
        cursor.execute("SELECT * FROM clients WHERE id = ?", (client["id"],))
        return cursor.fetchone()

    return client


def check_quota(conn: sqlite3.Connection, client: sqlite3.Row) -> Tuple[bool, Optional[str], sqlite3.Row]:
    """
    Checks whether a new session is allowed to start for this org.

    Returns (allowed, reason_if_blocked, refreshed_client_row).
    Caller is responsible for committing if the cycle was reset.
    """
    cursor = conn.cursor()
    client = _reset_billing_cycle_if_needed(cursor, client)
    conn.commit()

    if not client["is_active"]:
        return False, "Organization subscription is inactive.", client

    quota = client["monthly_quota"] or 0
    usage = client["monthly_usage_counter"] or 0

    if quota > 0 and usage >= quota:
        return False, (
            f"Monthly credit limit exceeded ({usage}/{quota} verifications used "
            f"this billing cycle)."
        ), client

    return True, None, client


def increment_usage(conn: sqlite3.Connection, client_id: int, terminal_state: str) -> None:
    """
    Increments the org's monthly_usage_counter and total_scans, but ONLY for
    sessions that reached a billable terminal state.
    """
    if terminal_state not in BILLABLE_TERMINAL_STATES:
        return

    cursor = conn.cursor()
    cursor.execute(
        """UPDATE clients
           SET monthly_usage_counter = monthly_usage_counter + 1,
               total_scans = total_scans + 1
           WHERE id = ?""",
        (client_id,)
    )
    conn.commit()
    logger.info(f"Billing: incremented usage counter for client_id={client_id} "
                 f"(terminal_state={terminal_state}).")


def estimate_cost_usd(client: sqlite3.Row) -> float:
    """
    Rough cost estimate for the current billing cycle, for display/reporting
    only — not a substitute for real invoicing.
    """
    tier = client["pricing_tier"]
    usage = client["monthly_usage_counter"] or 0

    if tier == TIER_GROWTH:
        overage = max(0, usage - GROWTH_INCLUDED_VERIFICATIONS)
        return round(150.0 + overage * UNIT_PRICE_USD["GROWTH_OVERAGE"], 2)
    if tier == TIER_ENTERPRISE:
        return 0.0  # custom contract, not modeled here
    return round(usage * UNIT_PRICE_USD["PAY_AS_YOU_GO"], 2)
