"""
app/services/session_store.py
Stateless storage service utilizing boto3 for AWS S3 / MinIO compatibility.
"""

import json
import logging
from typing import Dict, Any, Optional
import boto3
from botocore.exceptions import ClientError
from fastapi import HTTPException
from app.core.config import settings

logger = logging.getLogger("PakVerify.SessionStore")

# FSM States Constants
STATE_INITIATED = "INITIATED"
STATE_FRONT_COMPLETED = "FRONT_COMPLETED"
STATE_BACK_COMPLETED = "BACK_COMPLETED"
STATE_PROCESSING_BIOMETRICS = "PROCESSING_BIOMETRICS"
STATE_VERIFIED = "VERIFIED"
STATE_REJECTED = "REJECTED"
STATE_SPOOF_DETECTED = "SPOOF_DETECTED"

TERMINAL_STATES = {STATE_VERIFIED, STATE_REJECTED, STATE_SPOOF_DETECTED}

# Initialize S3 Client
s3_kwargs = {
    "aws_access_key_id": settings.S3_ACCESS_KEY,
    "aws_secret_access_key": settings.S3_SECRET_KEY,
    "region_name": settings.S3_REGION,
}
if settings.S3_ENDPOINT_URL:
    s3_kwargs["endpoint_url"] = settings.S3_ENDPOINT_URL

s3_client = boto3.client("s3", **s3_kwargs)


def upload_session_file(session_id: str, filename: str, file_bytes: bytes, content_type: str = "image/jpeg") -> str:
    """
    Streams file bytes directly to the configured S3/MinIO bucket.
    Returns the final S3 Object Key.
    """
    object_key = f"sessions/{session_id}/{filename}"
    try:
        s3_client.put_object(
            Bucket=settings.S3_BUCKET_NAME,
            Key=object_key,
            Body=file_bytes,
            ContentType=content_type
        )
        logger.info(f"Successfully uploaded {object_key} to S3.")
        return object_key
    except ClientError as e:
        logger.error(f"Failed uploading {object_key} to S3: {e}")
        raise HTTPException(status_code=500, detail="Cloud storage upload failure.")


def get_session_file_bytes(object_key: str) -> bytes:
    """
    Retrieves the raw image bytes from S3 for the Celery worker processing pipeline.
    """
    try:
        response = s3_client.get_object(Bucket=settings.S3_BUCKET_NAME, Key=object_key)
        return response["Body"].read()
    except ClientError as e:
        logger.error(f"Failed fetching S3 object {object_key}: {e}")
        raise FileNotFoundError(f"S3 Object key {object_key} missing.")


def hard_delete_session_files(session_id: str) -> None:
    """
    Zero-Trust enforcement: Deletes all images belonging to a session from S3
    once processing hits a terminal state.
    """
    prefix = f"sessions/{session_id}/"
    try:
        response = s3_client.list_objects_v2(Bucket=settings.S3_BUCKET_NAME, Prefix=prefix)
        if "Contents" not in response:
            return

        delete_keys = [{"Key": obj["Key"]} for obj in response["Contents"]]
        if delete_keys:
            s3_client.delete_objects(
                Bucket=settings.S3_BUCKET_NAME,
                Delete={"Objects": delete_keys}
            )
            logger.info(f"Zero-Trust complete: Purged {len(delete_keys)} objects for session {session_id}.")
    except ClientError as e:
        logger.error(f"Error executing zero-trust S3 wipe for session {session_id}: {e}")


# ── FSM & DB Database State Layer Helpers ─────────────────────────────────────

def create_session(conn, client_id: int) -> str:
    import uuid
    session_id = str(uuid.uuid4())
    conn.execute(
        "INSERT INTO sessions (session_id, client_id, state) VALUES (?, ?, ?)",
        (session_id, client_id, STATE_INITIATED)
    )
    conn.commit()
    return session_id


def get_session(conn, session_id: str, client_id: int) -> Dict[str, Any]:
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ? AND client_id = ?",
        (session_id, client_id)
    ).fetchone()
    if not row:
        raise HTTPException(status_code=404, detail="Session context missing.")
    return dict(row)


def require_state(session: Dict[str, Any], step: str):
    state = session["state"]
    if step == "front" and state != STATE_INITIATED:
        raise HTTPException(status_code=400, detail="Invalid state for front capture step.")
    elif step == "back" and state != STATE_FRONT_COMPLETED:
        raise HTTPException(status_code=400, detail="Invalid state for back capture step.")
    elif step == "biometrics" and state != STATE_BACK_COMPLETED:
        raise HTTPException(status_code=400, detail="Invalid state for biometrics submission.")


def transition(conn, session_id: str, from_state: str, to_state: str):
    cursor = conn.execute(
        "UPDATE sessions SET state = ?, updated_at = CURRENT_TIMESTAMP WHERE session_id = ? AND state = ?",
        (to_state, session_id, from_state)
    )
    if cursor.rowcount == 0:
        raise HTTPException(status_code=409, detail="FSM validation conflict detected.")
    conn.commit()


def merge_extracted_data(conn, session_id: str, data: Dict[str, Any]):
    current = conn.execute("SELECT extracted_data FROM sessions WHERE session_id = ?", (session_id,)).fetchone()
    existing = json.loads(current["extracted_data"]) if current and current["extracted_data"] else {}
    existing.update(data)
    
    cnic_number = data.get("extracted", {}).get("cnic_number")
    if cnic_number:
        conn.execute(
            "UPDATE sessions SET extracted_data = ?, cnic_number = ? WHERE session_id = ?",
            (json.dumps(existing), cnic_number, session_id)
        )
    else:
        conn.execute(
            "UPDATE sessions SET extracted_data = ? WHERE session_id = ?",
            (json.dumps(existing), session_id)
        )
    conn.commit()


def save_biometric_result(conn, session_id: str, result: Dict[str, Any]):
    conn.execute(
        "UPDATE sessions SET biometric_result = ? WHERE session_id = ?",
        (json.dumps(result), session_id)
    )
    conn.commit()