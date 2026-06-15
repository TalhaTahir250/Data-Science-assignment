from pydantic import BaseModel, Field
from typing import Optional, Dict, Any
from datetime import datetime


class SessionInitResponse(BaseModel):
    session_id: str
    state: str
    organization_id: str
    pricing_tier: str


class CaptureStepResponse(BaseModel):
    session_id: str
    state: str = Field(..., description="Session state AFTER this call")
    passed: bool = Field(..., description="Whether this capture step passed quality/validation checks")
    verdict: str
    failures: list = Field(default_factory=list)
    observations: Dict[str, Any] = Field(default_factory=dict)
    extracted: Dict[str, Any] = Field(default_factory=dict)


class BiometricsAcceptedResponse(BaseModel):
    session_id: str
    state: str
    message: str = "Biometric verification is processing. The result will be delivered to your configured webhook."


class SessionStatusResponse(BaseModel):
    session_id: str
    state: str
    organization_id: str
    cnic_number: Optional[str] = None
    extracted: Optional[Dict[str, Any]] = None
    biometrics: Optional[Dict[str, Any]] = None
    created_at: Optional[str] = None
    updated_at: Optional[str] = None


class WebhookPayload(BaseModel):
    session_id: str
    organization_id: str
    state: str
    timestamp: datetime
    cnic_number: Optional[str] = None
    extracted: Dict[str, Any] = Field(default_factory=dict)
    biometrics: Dict[str, Any] = Field(default_factory=dict)


class ErrorResponse(BaseModel):
    detail: str
