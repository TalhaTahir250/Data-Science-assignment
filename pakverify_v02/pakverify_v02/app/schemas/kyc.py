from pydantic import BaseModel, Field
from typing import Optional, Dict, Any, List
from datetime import datetime

class BiometricResult(BaseModel):
    biometric_status: str = Field(..., description="SUCCESS, FAILED_DETECTION, or ERROR")
    is_match: bool = Field(..., description="True if the face on the ID matches the selfie")
    distance_metric: float = Field(..., description="Mathematical distance between face vectors")
    threshold: float = Field(..., description="The passing score limit")
    match_strength: str = Field(..., description="STRONG MATCH, BORDERLINE MATCH, etc.")
    risk_level: str = Field(..., description="LOW, MEDIUM, or HIGH RISK")
    cropped_selfie_b64: Optional[str] = Field(None, description="Base64 encoded cropped selfie face")
    cropped_cnic_b64: Optional[str] = Field(None, description="Base64 encoded cropped ID face")
    error: Optional[str] = Field(None, description="Error message if detection failed")

class ValidationDetails(BaseModel):
    verdict: str
    passed_checks: int
    failed_checks: int
    total_checks: int
    failures: List[str]
    checks: Dict[str, Any]

class VerificationResponse(BaseModel):
    status: str = Field(..., description="VERIFIED, REVIEW, or REJECTED")
    verified: bool = Field(..., description="Ultimate boolean result for the client to process")
    timestamp: datetime
    sides_processed: str
    extracted: Dict[str, Any] = Field(..., description="All text parsed from the ID card")
    confidence: Dict[str, Any] = Field(..., description="AI confidence scores for the extraction")
    image_observations: Dict[str, Any] = Field(..., description="Security checks like glare or holograms")
    biometrics: BiometricResult
    validation: ValidationDetails

class ErrorResponse(BaseModel):
    detail: str = Field(..., description="Human readable explanation of what went wrong")