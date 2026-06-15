"""
app/api/v1/sessions.py

Sequential step-by-step verification flow (Technical Master Brief v0.2,
section 2), wired to the multi-tenant billing engine (section 3), the
async webhook dispatcher (section 4), and the ephemeral-storage hard
deletion policy (section 5).

Endpoints
---------
POST /v1/sessions                          -> initiate a session (402 if blocked)
POST /v1/sessions/{id}/document/front      -> front CNIC capture + OCR
POST /v1/sessions/{id}/document/back       -> back CNIC capture + Urdu fields
POST /v1/sessions/{id}/biometrics          -> selfie; 202 Accepted, async processing
GET  /v1/sessions/{id}                     -> poll current session status
"""

import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, File, UploadFile, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import get_db_connection
from app.core.auth import get_authenticated_client
from app.core.security import RateLimitShield
from app.schemas.session import (
    SessionInitResponse, CaptureStepResponse, BiometricsAcceptedResponse,
    SessionStatusResponse,
)
from app.services import session_store as ss
from app.services.extractor import GeminiExtractor
from app.services.biometrics import verify_face_biometrics
from app.utils import billing
from app.utils.webhooks import dispatch_webhook

logger = logging.getLogger("PakVerify.Sessions")
router = APIRouter()

# ── Shared extractor instance (lazy-initialized, mirrors app/api/v1/verify.py) ──
_extractor: Optional[GeminiExtractor] = None


def get_extractor() -> GeminiExtractor:
    global _extractor
    if _extractor is None:
        if not settings.GEMINI_API_KEY:
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
        _extractor = GeminiExtractor(settings.GEMINI_API_KEY)
    return _extractor


# ── Small filesystem helpers ──

def _save_upload(session_dir: Path, prefix: str, upload: UploadFile, data: bytes) -> Path:
    suffix = Path(upload.filename or "").suffix or ".jpg"
    dest = session_dir / f"{prefix}{suffix}"
    with open(dest, "wb") as f:
        f.write(data)
    return dest


def _find_file(session_dir: Path, prefix: str) -> Optional[Path]:
    matches = sorted(session_dir.glob(f"{prefix}.*"))
    return matches[0] if matches else None


def _enforce_size_limit(data: bytes) -> None:
    max_bytes = settings.MAX_FILE_MB * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"Image exceeds the {settings.MAX_FILE_MB}MB limit."
        )


def _capture_passed(validation: dict, observations: dict) -> bool:
    """
    Instant pass/fail gate for a document-capture step, based on image
    clarity, text legibility, and card detection (brief section 2,
    steps 2 & 3) — independent of the deeper field-level validation that
    only matters once both sides are in.
    """
    if observations.get("card_detected") is False:
        return False
    if str(observations.get("text_clarity", "")).lower() in ("poor", "unreadable"):
        return False
    if validation.get("verdict") == "REJECTED":
        return False
    return True


# ── 1. Session Initiation ──

@router.post("/", response_model=SessionInitResponse, dependencies=[Depends(RateLimitShield)])
async def initiate_session(client=Depends(get_authenticated_client)):
    conn = get_db_connection()
    try:
        allowed, reason, client = billing.check_quota(conn, client)
        if not allowed:
            raise HTTPException(status_code=402, detail=reason)

        session_id = ss.create_session(conn, client["id"])

        return SessionInitResponse(
            session_id=session_id,
            state=ss.STATE_INITIATED,
            organization_id=client["organization_id"],
            pricing_tier=client["pricing_tier"],
        )
    finally:
        conn.close()


# ── 2. Front CNIC Capture & Optical OCR ──

@router.post("/{session_id}/document/front", response_model=CaptureStepResponse,
             dependencies=[Depends(RateLimitShield)])
async def capture_front(session_id: str, image: UploadFile = File(...),
                         client=Depends(get_authenticated_client)):
    conn = get_db_connection()
    try:
        session = ss.get_session(conn, session_id, client["id"])
        ss.require_state(session, "front")

        data = await image.read()
        _enforce_size_limit(data)

        session_dir = ss.get_session_dir(session_id)
        front_path = _save_upload(session_dir, "front", image, data)

        result = get_extractor().extract(str(front_path))
        if result.get("status") == "error":
            # Transient OCR/service error — session stays INITIATED so the
            # client can retry the same step without losing progress.
            raise HTTPException(status_code=503, detail=result.get("error", "OCR engine unavailable."))

        validation = result.get("validation", {})
        observations = result.get("observations", {})
        passed = _capture_passed(validation, observations)

        ss.merge_extracted_data(conn, session_id, {
            "extracted": result.get("extracted", {}),
            "confidence": result.get("confidence", {}),
            "observations": observations,
            "front_validation": validation,
        })

        if passed:
            ss.transition(conn, session_id, ss.STATE_INITIATED, ss.STATE_FRONT_COMPLETED)
            new_state = ss.STATE_FRONT_COMPLETED
        else:
            new_state = ss.STATE_INITIATED  # retry the same step

        return CaptureStepResponse(
            session_id=session_id,
            state=new_state,
            passed=passed,
            verdict=validation.get("verdict", "UNKNOWN"),
            failures=validation.get("failures", []),
            observations=observations,
            extracted=result.get("extracted", {}),
        )
    finally:
        conn.close()


# ── 3. Back CNIC Capture & Urdu Translation ──

@router.post("/{session_id}/document/back", response_model=CaptureStepResponse,
              dependencies=[Depends(RateLimitShield)])
async def capture_back(session_id: str, image: UploadFile = File(...),
                        client=Depends(get_authenticated_client)):
    conn = get_db_connection()
    try:
        session = ss.get_session(conn, session_id, client["id"])
        ss.require_state(session, "back")

        data = await image.read()
        _enforce_size_limit(data)

        session_dir = ss.get_session_dir(session_id)
        front_path = _find_file(session_dir, "front")
        if not front_path:
            raise HTTPException(status_code=409, detail="Front image missing for this session; restart the capture flow.")

        back_path = _save_upload(session_dir, "back", image, data)

        # Re-run extraction across BOTH images: this is what produces the
        # Urdu address/name fields and province validation (brief step 3).
        result = get_extractor().extract(str(front_path), str(back_path))
        if result.get("status") == "error":
            raise HTTPException(status_code=503, detail=result.get("error", "OCR engine unavailable."))

        validation = result.get("validation", {})
        observations = result.get("observations", {})
        passed = _capture_passed(validation, observations)

        ss.merge_extracted_data(conn, session_id, {
            "extracted": result.get("extracted", {}),
            "confidence": result.get("confidence", {}),
            "observations": observations,
            "back_validation": validation,
        })

        if passed:
            ss.transition(conn, session_id, ss.STATE_FRONT_COMPLETED, ss.STATE_BACK_COMPLETED)
            new_state = ss.STATE_BACK_COMPLETED
        else:
            new_state = ss.STATE_FRONT_COMPLETED  # retry the back capture

        return CaptureStepResponse(
            session_id=session_id,
            state=new_state,
            passed=passed,
            verdict=validation.get("verdict", "UNKNOWN"),
            failures=validation.get("failures", []),
            observations=observations,
            extracted=result.get("extracted", {}),
        )
    finally:
        conn.close()


# ── 4. Passive Liveness Gating & Biometrics (async) ──

@router.post("/{session_id}/biometrics", response_model=BiometricsAcceptedResponse,
              status_code=202, dependencies=[Depends(RateLimitShield)])
async def submit_biometrics(session_id: str, background_tasks: BackgroundTasks,
                             selfie: UploadFile = File(...),
                             client=Depends(get_authenticated_client)):
    conn = get_db_connection()
    try:
        session = ss.get_session(conn, session_id, client["id"])
        ss.require_state(session, "biometrics")

        data = await selfie.read()
        _enforce_size_limit(data)

        session_dir = ss.get_session_dir(session_id)
        _save_upload(session_dir, "selfie", selfie, data)

        # Non-blocking: ack immediately, hand the heavy DeepFace work to a
        # background task, and deliver the result via webhook (brief section 4).
        ss.transition(conn, session_id, ss.STATE_BACK_COMPLETED, ss.STATE_PROCESSING_BIOMETRICS)

        background_tasks.add_task(_process_biometrics, session_id, client["id"])

        return BiometricsAcceptedResponse(
            session_id=session_id,
            state=ss.STATE_PROCESSING_BIOMETRICS,
        )
    finally:
        conn.close()


def _process_biometrics(session_id: str, client_id: int) -> None:
    """
    Runs off the request/response cycle (FastAPI BackgroundTask).

    Defined as a regular (sync) function so FastAPI executes it in a
    threadpool — keeping the blocking DeepFace calls off the event loop
    without any extra plumbing.
    """
    conn = get_db_connection()
    try:
        session = conn.execute(
            "SELECT * FROM sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        client = conn.execute(
            "SELECT * FROM clients WHERE id = ?", (client_id,)
        ).fetchone()
        if not session or not client:
            logger.error(f"Background biometrics: session or client vanished for {session_id}")
            return

        session_dir = ss.get_session_dir(session_id)
        front_path = _find_file(session_dir, "front")
        selfie_path = _find_file(session_dir, "selfie")

        biometric_result = verify_face_biometrics(str(selfie_path), str(front_path))
        ss.save_biometric_result(conn, session_id, biometric_result)

        status = biometric_result.get("biometric_status")
        if status == "SPOOF_DETECTED":
            terminal_state = ss.STATE_SPOOF_DETECTED
        elif biometric_result.get("is_match") is True:
            terminal_state = ss.STATE_VERIFIED
        else:
            terminal_state = ss.STATE_REJECTED

        ss.transition(conn, session_id, ss.STATE_PROCESSING_BIOMETRICS, terminal_state)

        # ── Billing: increment usage counter ONLY for terminal states ──
        billing.increment_usage(conn, client_id, terminal_state)

        # ── Audit ledger ──
        import json as _json
        extracted_blob = _json.loads(session["extracted_data"]) if session["extracted_data"] else {}
        cnic_number = extracted_blob.get("extracted", {}).get("cnic_number", "UNKNOWN")
        conn.execute(
            """INSERT INTO scan_logs (client_id, cnic_number, status, match_strength, risk_level, session_id)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (client_id, cnic_number, terminal_state,
             biometric_result.get("match_strength", "UNKNOWN"),
             biometric_result.get("risk_level", "UNKNOWN"), session_id)
        )
        conn.commit()

        # ── Webhook dispatch (signed with org's webhook_secret) ──
        payload = {
            "session_id": session_id,
            "organization_id": client["organization_id"],
            "state": terminal_state,
            "timestamp": datetime.utcnow().isoformat(),
            "cnic_number": cnic_number,
            "extracted": extracted_blob.get("extracted", {}),
            "biometrics": biometric_result,
        }
        dispatch_webhook(client["webhook_url"], client["webhook_secret"], payload)

        # ── Hard deletion: terminal state reached + dispatch attempted ──
        ss.hard_delete_session_files(session_id)

    except Exception:
        logger.exception(f"Background biometrics processing failed for session {session_id}")
    finally:
        conn.close()


# ── 5. Session status polling ──

@router.get("/{session_id}", response_model=SessionStatusResponse)
async def get_session_status(session_id: str, client=Depends(get_authenticated_client)):
    conn = get_db_connection()
    try:
        session = ss.get_session(conn, session_id, client["id"])

        import json as _json
        extracted = _json.loads(session["extracted_data"]) if session["extracted_data"] else None
        biometrics = _json.loads(session["biometric_result"]) if session["biometric_result"] else None

        return SessionStatusResponse(
            session_id=session["session_id"],
            state=session["state"],
            organization_id=client["organization_id"],
            cnic_number=session["cnic_number"],
            extracted=extracted,
            biometrics=biometrics,
            created_at=session["created_at"],
            updated_at=session["updated_at"],
        )
    finally:
        conn.close()
