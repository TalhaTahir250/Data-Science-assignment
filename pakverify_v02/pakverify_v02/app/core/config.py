"""
app/core/config.py
Extended for Phase 2 with AWS S3 / MinIO storage configuration.
"""

import os
import logging
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    # ── Existing App Config ──────────────────────────────────────────────────
    APP_NAME: str = "PakVerify API"
    VERSION:  str = "1.0.0"
    DEBUG:    bool = False
    RATE_LIMIT_RPM: int = 20

    # ── Gemini & DeepFace ────────────────────────────────────────────────────
    GEMINI_MODEL:  str = "gemini-2.5-flash"
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEEPFACE_MODEL:     str   = "ArcFace"
    DISTANCE_THRESHOLD: float = 0.68

    # ── Security & Webhooks ──────────────────────────────────────────────────
    API_KEY:    str = os.getenv("API_KEY", "pakverify-v01-key")
    MAX_FILE_MB: int = 10
    LEDGER_PATH:         str = "audit_ledger.csv"
    SESSION_TTL_MINUTES: int = 30
    WEBHOOK_TIMEOUT_SECONDS: float = 5.0
    WEBHOOK_MAX_RETRIES:     int   = 2

    # ── Redis / Celery (Phase 1) ─────────────────────────────────────────────
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    REDIS_RESULT_BACKEND: str = os.getenv("REDIS_RESULT_BACKEND", "redis://localhost:6379/1")

    # ── AWS S3 / MinIO Storage (Phase 2 - New) ────────────────────────────────
    S3_ENDPOINT_URL: str = os.getenv("S3_ENDPOINT_URL", "")  # Keep empty for real AWS S3; use http://localhost:9000 for MinIO
    S3_ACCESS_KEY: str = os.getenv("S3_ACCESS_KEY", "minioadmin")
    S3_SECRET_KEY: str = os.getenv("S3_SECRET_KEY", "minioadmin")
    S3_BUCKET_NAME: str = os.getenv("S3_BUCKET_NAME", "pakverify-sessions")
    S3_REGION: str = os.getenv("S3_REGION", "us-east-1")

    class Config:
        env_file = ".env"
        extra    = "ignore"


settings = Settings()

# Global logging configuration
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("app.log", mode="a"),
    ],
)
logger = logging.getLogger("PakVerify")