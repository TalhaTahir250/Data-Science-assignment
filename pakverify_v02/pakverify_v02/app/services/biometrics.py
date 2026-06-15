import logging
import cv2
import base64
from typing import Dict, Any
from deepface import DeepFace
from app.core.config import settings

logger = logging.getLogger("PakVerify.Biometrics")

def verify_face_biometrics(selfie_path: str, cnic_front_path: str) -> Dict[str, Any]:
    """Runs local biometric verification, calculates risk, and extracts cropped faces."""
    try:
        # ── Liveness / Anti-Spoofing Check (Selfie ONLY) ──
        # IMPORTANT: anti_spoofing must NOT be passed to DeepFace.verify() against
        # the CNIC image. The CNIC photo is itself a flat, printed/laminated
        # surface — the anti-spoof model would near-certainly flag it as a
        # "spoof" and fail every single verification. So we run the spoof
        # check as a separate, dedicated step on the selfie only.
        try:
            selfie_faces = DeepFace.extract_faces(
                img_path=selfie_path,
                enforce_detection=False,
                anti_spoofing=True
            )
            if selfie_faces:
                face0 = selfie_faces[0]
                is_real = face0.get("is_real", True)
                antispoof_score = face0.get("antispoof_score")
                if not is_real:
                    logger.warning(f"Liveness check FAILED on selfie (antispoof_score={antispoof_score}).")
                    return {
                        "biometric_status": "SPOOF_DETECTED",
                        "is_match": False,
                        "distance_metric": 0.0,
                        "threshold": settings.DISTANCE_THRESHOLD,
                        "match_strength": "LIVENESS CHECK FAILED",
                        "risk_level": "HIGH RISK",
                        "antispoof_score": antispoof_score,
                        "error": "Selfie failed liveness check — appears to be a photo of a screen, printed photo, or other spoof."
                    }
        except Exception as spoof_err:
            # Don't hard-fail verification if the anti-spoof model itself errors
            # (e.g. weights not yet downloaded) — log and continue to face match.
            logger.warning(f"Liveness check could not be completed, continuing without it: {spoof_err}")

        logger.info(f"Biometrics: Executing local DeepFace comparison pipeline ({settings.DEEPFACE_MODEL}).")
        
        # Run verification using settings constants
        result = DeepFace.verify(
            img1_path=selfie_path,
            img2_path=cnic_front_path,
            model_name=settings.DEEPFACE_MODEL,
            distance_metric="cosine",
            enforce_detection=False
        )
        
        distance = result.get("distance", 1.0)
        threshold = result.get("threshold", settings.DISTANCE_THRESHOLD)
        is_match = result.get("verified", False)

        # With enforce_detection=False, DeepFace won't raise on a missing face —
        # it silently returns degenerate facial_areas (zero width/height) instead.
        areas = result.get("facial_areas", {})
        area1 = areas.get("img1", {})
        area2 = areas.get("img2", {})
        face_missing = (not area1.get("w")) or (not area2.get("w"))

        if face_missing:
            return {
                "biometric_status": "FAILED_DETECTION",
                "is_match": False,
                "distance_metric": round(distance, 4),
                "threshold": threshold,
                "match_strength": "DETECTION FAILED",
                "risk_level": "HIGH RISK",
                "error": "Could not firmly isolate faces. Ensure clear visibility without glare."
            }
        
        # ── Enterprise Risk Categorization ──
        if is_match:
            if distance <= (threshold * 0.60):    # Very strong match
                match_strength = "STRONG MATCH"
                risk_level = "LOW RISK"
            elif distance <= (threshold * 0.85):  # Good match
                match_strength = "MODERATE MATCH"
                risk_level = "LOW RISK"
            else:                                 # Close to the threshold edge
                match_strength = "BORDERLINE MATCH"
                risk_level = "MEDIUM RISK"
        else:
            match_strength = "NO MATCH"
            risk_level = "HIGH RISK"

        # ── Dynamic Face Cropping ──
        selfie_b64 = None
        cnic_face_b64 = None
        
        try:
            areas = result.get("facial_areas", {})
            area1 = areas.get("img1", {})  # Selfie
            area2 = areas.get("img2", {})  # CNIC
            
            if area1 and area2:
                # Read original images into OpenCV
                img1 = cv2.imread(selfie_path)
                img2 = cv2.imread(cnic_front_path)
                
                # Crop selfie face
                crop1 = img1[area1['y']:area1['y']+area1['h'], area1['x']:area1['x']+area1['w']]
                _, buffer1 = cv2.imencode('.jpg', crop1)
                selfie_b64 = base64.b64encode(buffer1).decode('utf-8')
                
                # Crop CNIC face
                crop2 = img2[area2['y']:area2['y']+area2['h'], area2['x']:area2['x']+area2['w']]
                _, buffer2 = cv2.imencode('.jpg', crop2)
                cnic_face_b64 = base64.b64encode(buffer2).decode('utf-8')
        except Exception as crop_err:
            logger.warning(f"Failed to generate facial crop assets: {crop_err}")

        return {
            "biometric_status": "SUCCESS",
            "is_match": is_match,
            "distance_metric": round(distance, 4),
            "threshold": threshold,
            "match_strength": match_strength,
            "risk_level": risk_level,
            "cropped_selfie_b64": selfie_b64,
            "cropped_cnic_b64": cnic_face_b64
        }
        
    except ValueError as detection_err:
        logger.warning(f"Biometrics detection halt: {detection_err}")
        return {
            "biometric_status": "FAILED_DETECTION",
            "is_match": False,
            "distance_metric": 0.0,
            "threshold": settings.DISTANCE_THRESHOLD,
            "match_strength": "DETECTION FAILED",
            "risk_level": "HIGH RISK",
            "error": "Could not firmly isolate faces. Ensure clear visibility without glare."
        }
    except Exception as e:
        logger.error(f"Biometric pipeline error: {e}")
        return {
            "biometric_status": "ERROR",
            "is_match": False,
            "distance_metric": 0.0,
            "threshold": settings.DISTANCE_THRESHOLD,
            "match_strength": "SYSTEM ERROR",
            "risk_level": "HIGH RISK",
            "error": str(e)
        }