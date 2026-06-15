import sqlite3
import logging
from fastapi import Header, HTTPException

from app.core.database import get_db_connection

logger = logging.getLogger("PakVerify.Auth")


def get_authenticated_client(x_api_key: str = Header(default=None)) -> sqlite3.Row:
    """
    Shared tenant-authentication dependency. Looks up the organization
    (client) row by X-API-Key header.

    Note: this checks that the API key exists, but NOT subscription status
    or quota — those are billing concerns checked separately via
    app.utils.billing.check_quota() at session-initiation time, so that an
    inactive/over-quota org still gets a clear 402 rather than a generic 401.
    """
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")

    conn = get_db_connection()
    try:
        client = conn.execute(
            "SELECT * FROM clients WHERE api_key = ?", (x_api_key,)
        ).fetchone()
    finally:
        conn.close()

    if not client:
        raise HTTPException(status_code=401, detail="Invalid API Key")

    return client
