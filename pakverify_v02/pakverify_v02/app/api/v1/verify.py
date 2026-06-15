import os
import tempfile
import logging
from pathlib import Path
from datetime import datetime

# Added "Depends" to FastAPI imports
from fastapi import APIRouter, File, UploadFile, HTTPException, Header, Request, Depends
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.database import get_db_connection
from app.core.security import RateLimitShield  # <-- IMPORT THE SHIELD
from app.schemas.kyc import VerificationResponse
from app.services.extractor import GeminiExtractor
from app.services.biometrics import verify_face_biometrics

logger = logging.getLogger("PakVerify.VerifyRoute")
router = APIRouter()

_extractor = None
def get_extractor():
    global _extractor
    if _extractor is None:
        if not settings.GEMINI_API_KEY:
            raise HTTPException(status_code=503, detail="GEMINI_API_KEY not configured")
        _extractor = GeminiExtractor(settings.GEMINI_API_KEY)
    return _extractor

# Added RateLimitShield as a dependency here
@router.post("/", response_model=VerificationResponse, dependencies=[Depends(RateLimitShield)])
async def verify_identity(
    request: Request,
    front: UploadFile = File(..., description="CNIC front side"),
    selfie: UploadFile = File(..., description="Live verification selfie"),
    back: UploadFile = File(None, description="CNIC back side"),
    x_api_key: str = Header(default=None),
):
    # ── 1. REAL DATABASE AUTHENTICATION & BILLING CHECK ──
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing X-API-Key header")
        
    conn = get_db_connection()
    client = conn.execute(
        "SELECT id, company_name FROM clients WHERE api_key = ? AND is_active = 1", 
        (x_api_key,)
    ).fetchone()
    
    if not client:
        conn.close()
        raise HTTPException(status_code=401, detail="Invalid or suspended API Key")
    
    client_id = client['id']
    client_name = client['company_name']
    logger.info(f"Auth Success: Request from Client [{client_name}]")
    logger.info(f"VERIFY REQUEST | front={front.filename} | selfie={selfie.filename}")

    front_tmp = selfie_tmp = back_tmp = None

    try:
        # ── 2. SAVE ASSETS TEMPORARILY ──
        suffix_f = Path(front.filename).suffix or ".jpg"
        front_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix_f).name
        with open(front_tmp, "wb") as f: 
            f.write(await front.read())

        suffix_s = Path(selfie.filename).suffix or ".jpg"
        selfie_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix_s).name
        with open(selfie_tmp, "wb") as f: 
            f.write(await selfie.read())

        if back and back.filename:
            suffix_b = Path(back.filename).suffix or ".jpg"
            back_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=suffix_b).name
            with open(back_tmp, "wb") as f: 
                f.write(await back.read())

        # ── 3. RUN COGNITIVE ENGINE & BIOMETRICS ──
        extractor = get_extractor()
        ocr_result = extractor.extract(front_tmp, back_tmp)
        
        if ocr_result.get("status") == "error":
            raise HTTPException(status_code=422, detail=ocr_result.get("error", "OCR Failed"))

        biometric_result = verify_face_biometrics(selfie_tmp, front_tmp)

        # ── 4. CALCULATE ENHANCED VERDICT MATRIX ──
        ocr_validation = ocr_result.get("validation", {})
        ocr_verdict = ocr_validation.get("verdict", "UNKNOWN")
        
        overall_verified = (ocr_verdict == "VERIFIED") and (biometric_result.get("is_match") is True)
        final_verdict = "VERIFIED" if overall_verified else ("REJECTED" if ocr_verdict == "REJECTED" else "REVIEW")

        extracted_data = ocr_result.get("extracted", {})

        # ── 5. LOG TO SQL DATABASE & ACCRUE BILLING UNITS ──
        try:
            conn.execute('''
                INSERT INTO scan_logs (client_id, cnic_number, status, match_strength, risk_level)
                VALUES (?, ?, ?, ?, ?)
            ''', (
                client_id, 
                extracted_data.get("cnic_number", "UNKNOWN"), 
                final_verdict, 
                biometric_result.get("match_strength", "UNKNOWN"), 
                biometric_result.get("risk_level", "UNKNOWN")
            ))
            
            conn.execute("UPDATE clients SET total_scans = total_scans + 1 WHERE id = ?", (client_id,))
            conn.commit()
        except Exception as db_err:
            logger.error(f"Database transaction logging pipeline failed: {db_err}")
        finally:
            conn.close()

        # ── 6. ASSEMBLE PYDANTIC-COMPLIANT DICTIONARY PAYLOAD ──
        response_data = {
            "status": final_verdict,
            "verified": overall_verified,
            "timestamp": datetime.utcnow().isoformat(),
            "sides_processed": "front+back" if back_tmp else "front only",
            "extracted": extracted_data,
            "confidence": ocr_result.get("confidence", {}),
            "image_observations": ocr_result.get("observations", {}),
            "biometrics": biometric_result,
            "validation": {
                "verdict": final_verdict,
                "passed_checks": ocr_validation.get("passed", 0) + (1 if biometric_result.get("is_match") else 0),
                "failed_checks": ocr_validation.get("failed", 0) + (0 if biometric_result.get("is_match") else 1),
                "total_checks": ocr_validation.get("total_checks", 0) + 1,
                "failures": ocr_validation.get("failures", []) + ([biometric_result.get("error")] if biometric_result.get("error") else []),
                "checks": {**ocr_validation.get("checks", {}), "biometric_face_match": biometric_result.get("is_match")},
            },
        }

        return JSONResponse(content=response_data)

    finally:
        # ── 7. ATOMIC SECURITY SANITIZATION ──
        for tmp in (front_tmp, selfie_tmp, back_tmp):
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except Exception as e:
                    logger.warning(f"Failed to clear volatile disk buffer {tmp}: {e}")