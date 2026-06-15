"""
app/utils/webhooks.py

Outbound webhook dispatch for terminal session states (Technical Master
Brief v0.2, section 4).

Every payload is signed with HMAC-SHA256 using the receiving org's
webhook_secret, and the hex digest is sent in the `X-PakVerify-Signature`
header. This lets the business customer verify the payload genuinely came
from PakVerify and wasn't tampered with in transit.

Verification on the receiving end (reference implementation):

    import hmac, hashlib

    expected = hmac.new(
        webhook_secret.encode("utf-8"),
        raw_request_body,          # the exact bytes received, NOT re-serialized JSON
        hashlib.sha256
    ).hexdigest()

    if not hmac.compare_digest(expected, request.headers["X-PakVerify-Signature"]):
        reject()
"""

import hmac
import hashlib
import json
import logging
from typing import Any, Dict, Optional

import httpx

from app.core.config import settings

logger = logging.getLogger("PakVerify.Webhooks")

SIGNATURE_HEADER = "X-PakVerify-Signature"


def sign_payload(secret: str, payload_bytes: bytes) -> str:
    """Returns the hex HMAC-SHA256 digest of payload_bytes using secret."""
    return hmac.new(secret.encode("utf-8"), payload_bytes, hashlib.sha256).hexdigest()


def dispatch_webhook(webhook_url: Optional[str], webhook_secret: Optional[str],
                      payload: Dict[str, Any]) -> bool:
    """
    Synchronously POSTs `payload` as JSON to `webhook_url`, signed with
    `webhook_secret`. Intended to be called from a FastAPI BackgroundTask
    (which already runs off the request/response cycle), so a blocking
    httpx call here does not hold up the client's mobile app connection.

    Retries up to settings.WEBHOOK_MAX_RETRIES times on connection errors
    or 5xx responses. Returns True if the webhook was accepted (2xx),
    False otherwise (including if no webhook is configured).
    """
    if not webhook_url:
        logger.info("No webhook_url configured for this org; skipping dispatch.")
        return False

    # Canonical JSON bytes — signature is computed over exactly these bytes,
    # and the receiver must verify against the raw body they received.
    body = json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    signature = sign_payload(webhook_secret or "", body)

    headers = {
        "Content-Type": "application/json",
        SIGNATURE_HEADER: signature,
    }

    attempts = settings.WEBHOOK_MAX_RETRIES + 1
    for attempt in range(1, attempts + 1):
        try:
            response = httpx.post(
                webhook_url,
                content=body,
                headers=headers,
                timeout=settings.WEBHOOK_TIMEOUT_SECONDS,
            )
            if response.status_code < 300:
                logger.info(
                    f"Webhook delivered to {webhook_url} "
                    f"(session={payload.get('session_id')}, status={response.status_code})."
                )
                return True

            logger.warning(
                f"Webhook attempt {attempt}/{attempts} to {webhook_url} "
                f"returned {response.status_code}: {response.text[:200]}"
            )
        except httpx.HTTPError as exc:
            logger.warning(
                f"Webhook attempt {attempt}/{attempts} to {webhook_url} failed: {exc}"
            )

    logger.error(
        f"Webhook delivery to {webhook_url} failed after {attempts} attempts "
        f"(session={payload.get('session_id')})."
    )
    return False
