"""
PAK_VERIFY v1.0 — Optimized Gemini Vision Extractor
==================================================
Sends CNIC front/back images to Gemini 2.5 Flash.
Guarantees structured output formatting via native Pydantic schemas.
"""
import time
import json
import logging
import re
from datetime import date
from typing import List, Optional
from pydantic import BaseModel, Field

from google import genai
from google.genai import types
from PIL import Image

logger = logging.getLogger(__name__)

# ==========================================
# 1. Pydantic Schemas for Structured Output
# ==========================================

class ExtractedData(BaseModel):
    cnic_number: Optional[str] = Field(None, description="Format: DDDDD-NNNNNNN-D")
    name_english: Optional[str] = Field(None, description="Proper title case name")
    name_urdu: Optional[str] = None
    father_name_english: Optional[str] = Field(None, description="Proper title case father name")
    father_name_urdu: Optional[str] = None
    date_of_birth: Optional[str] = Field(None, description="Format: DD/MM/YYYY")
    date_of_issue: Optional[str] = Field(None, description="Format: DD/MM/YYYY")
    date_of_expiry: Optional[str] = Field(None, description="Format: DD/MM/YYYY, OR the literal string 'Lifetime' if the card shows 'Lifetime'/'حیات' as the expiry")
    gender: Optional[str] = Field(None, description="Must be exactly 'Male' or 'Female'")
    address: Optional[str] = None
    address_urdu: Optional[str] = None
    city: Optional[str] = None
    district: Optional[str] = None
    province: Optional[str] = Field(None, description="One of: Punjab, Sindh, KPK, Balochistan, Gilgit-Baltistan, AJK, ICT")
    country: str = "PAKISTAN"
    card_side: str = Field(..., description="Either 'FRONT' or 'BOTH'")
    mrz_line: Optional[str] = None

class GeminiConfidence(BaseModel):
    overall: float
    cnic_number: float
    name: float
    dates: float
    address: float

class ImageObservations(BaseModel):
    card_detected: bool
    image_quality: str
    is_color_photocopy: bool
    is_black_white_photocopy: bool
    is_screenshot: bool
    security_hologram_visible: bool
    card_edges_visible: bool
    text_clarity: str
    lighting: str
    anomalies: List[str]

class CnicExtractionResponse(BaseModel):
    extracted: ExtractedData
    gemini_confidence: GeminiConfidence
    image_observations: ImageObservations


# ==========================================
# 2. Optimized Prompts
# ==========================================

FRONT_ONLY_PROMPT = """
You are an expert Pakistani CNIC verification system.
Analyze this CNIC FRONT image and extract every visible field according to the provided schema guidelines.

STRICT RULES:
- If a field is not completely visible or missing, set it to null — NEVER guess.
- Fix common OCR vision errors: O->0, I->1, B->8, S->5, Z->2
- Dates must strictly follow the DD/MM/YYYY formatting rules.
- EXCEPTION: if the expiry date field shows "Lifetime" or the Urdu "حیات", set date_of_expiry to exactly the string "Lifetime" (do not attempt to convert it to a date).
- Format names to proper Title Case.
"""

BOTH_SIDES_PROMPT = """
You are an expert Pakistani CNIC verification system.
You are given two images representing an identity document. 

Your first internal task is to dynamically identify which image is the FRONT and which is the BACK.
- The FRONT contains the portrait photo, English/Urdu names, CNIC number, and Date of Birth.
- The BACK contains the current/permanent address tracking details and district data.

Extract all processing fields across both images into a single unified schema response.

STRICT RULES:
- If a field is not completely visible across either side, set it to null.
- Fix common OCR vision errors: O->0, I->1, B->8, S->5, Z->2
- Dates must strictly follow the DD/MM/YYYY formatting rules.
- EXCEPTION: if the expiry date field shows "Lifetime" or the Urdu "حیات", set date_of_expiry to exactly the string "Lifetime" (do not attempt to convert it to a date).
- Format names to proper Title Case.
"""


# ==========================================
# 3. Principal Extractor Engine Class
# ==========================================

class GeminiExtractor:

    def __init__(self, api_key: str):
        self.client = genai.Client(api_key=api_key)
        # Using stable current production version for operational predictability
        self.model  = "gemini-2.5-flash"
        logger.info("GeminiExtractor initialized smoothly with gemini-2.5-flash")

    def extract(self, front_path: str, back_path: Optional[str] = None) -> dict:
        try:
            front_img = Image.open(front_path)
            if back_path:
                back_img = Image.open(back_path)
                logger.info("Processing execution: Sending BOTH images.")
                contents = [BOTH_SIDES_PROMPT, front_img, back_img]
            else:
                logger.info("Processing execution: Front image extraction active.")
                contents = [FRONT_ONLY_PROMPT, front_img]

            # --- RETRY LOGIC FOR 503 ERRORS ---
            max_retries = 3
            base_delay = 2  # seconds
            response = None

            for attempt in range(max_retries):
                try:
                    response = self.client.models.generate_content(
                        model    = self.model,
                        contents = contents,
                        config   = types.GenerateContentConfig(
                            temperature       = 0.0,
                            max_output_tokens = 4096,
                            response_mime_type= "application/json",
                            response_schema   = CnicExtractionResponse,
                            thinking_config   = types.ThinkingConfig(thinking_budget=0),
                        )
                    )
                    # If successful, break out of the retry loop
                    break
                except Exception as api_error:
                    # Check if the error looks like a 503 or temporary unavailability
                    if "503" in str(api_error) or "UNAVAILABLE" in str(api_error).upper():
                        if attempt < max_retries - 1:
                            delay = base_delay * (2 ** attempt)  # 2s, then 4s, then 8s
                            logger.warning(f"Google API busy (503). Retrying in {delay} seconds (Attempt {attempt + 1}/{max_retries})...")
                            time.sleep(delay)
                            continue
                    # If it's a different error (like a bad key) or we ran out of retries, raise it
                    raise api_error

            if not response:
                return {"status": "error", "error": "Server is temporarily busy. Please try again in a moment."}
            # ----------------------------------

            # --- NUCLEAR JSON FAILSAFE ---
            # Gemini sometimes returns valid data but breaks JSON formatting by
            # embedding literal line breaks (common with multi-line Urdu addresses)
            # or wrapping the payload in markdown code fences. Strip both before
            # parsing so we don't randomly throw HTTP 422s on otherwise-good data.
            raw_text = response.text
            try:
                data = json.loads(raw_text)
            except json.JSONDecodeError:
                logger.warning("Standard JSON parse failed — engaging Nuclear Failsafe.")
                cleaned = raw_text.strip()
                # Strip markdown code fences (```json ... ``` or ``` ... ```)
                cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned)
                cleaned = re.sub(r"```\s*$", "", cleaned)
                # Strip literal line breaks / carriage returns / tabs that break JSON
                # when they appear inside string values (e.g. multi-line addresses)
                cleaned = cleaned.replace("\r\n", " ").replace("\n", " ").replace("\r", " ").replace("\t", " ")
                cleaned = cleaned.strip()
                data = json.loads(cleaned)
            logger.info("System structured object verification mapping accepted.")

            extracted = data.get("extracted", {})
            observations = data.get("image_observations", {})

            # --- GENDER NORMALIZATION ---
            # Gemini sometimes echoes the card's literal "M"/"F" instead of the
            # full "Male"/"Female" the schema asks for. Normalize defensively.
            gender_raw = extracted.get("gender")
            if gender_raw:
                g = str(gender_raw).strip().upper()
                if g in ("M", "MALE"):
                    extracted["gender"] = "Male"
                elif g in ("F", "FEMALE"):
                    extracted["gender"] = "Female"

            validation = self._validate(extracted, observations, has_back=bool(back_path))

            return {
                "status":       "success",
                "extracted":    extracted,
                "confidence":   data.get("gemini_confidence", {}),
                "observations": observations,
                "validation":   validation,
            }

        except Exception as e:
            logger.error(f"Execution runtime exception caught: {e}")
            if "503" in str(e) or "UNAVAILABLE" in str(e).upper():
                return {"status": "error", "error": "Google's verification engine is experiencing high demand. Please re-upload in 10 seconds."}
            return {"status": "error", "error": str(e)}

    def _validate(self, extracted: dict, observations: dict, has_back: bool) -> dict:
        checks   = {}
        failures = []

        # Database mapping rules for Pakistani Provincial Zones
        PROVINCE_CODES = {
            "1": "KPK",
            "2": "FATA",
            "3": "Punjab",
            "4": "Sindh",
            "5": "Balochistan",
            "6": "ICT",
            "7": "Gilgit-Baltistan"
        }

        cnic = extracted.get("cnic_number")
        if not cnic:
            checks["cnic_present"]        = False
            checks["cnic_format_valid"]   = False
            checks["cnic_district_valid"] = False
            failures.append("CNIC registration sequence footprint missing from image file asset.")
        else:
            checks["cnic_present"] = True
            pattern_ok = bool(re.match(r"^\d{5}-\d{7}-\d$", str(cnic)))
            checks["cnic_format_valid"] = pattern_ok
            
            if not pattern_ok:
                failures.append(f"Format sequence irregularity detected: '{cnic}' — expected pattern format: DDDDD-NNNNNNN-C")

            if pattern_ok:
                # Provincial Inference Fallback Logic
                first_digit = str(cnic)[0]
                if first_digit in PROVINCE_CODES:
                    inferred_province = PROVINCE_CODES[first_digit]
                    if not extracted.get("province"):
                        extracted["province"] = inferred_province
                        logger.info(f"Fallback Execution Active: Inferred missing provincial context '{inferred_province}' from routing key.")

                # District structural mapping boundaries
                district_code = int(cnic.split("-")[0])
                district_ok = 10000 <= district_code <= 79999
                checks["cnic_district_valid"] = district_ok
                if not district_ok:
                    failures.append(f"District code signature allocation scope context anomaly for range boundary: {district_code}")

                # NADRA Gender Identification Rule Configuration
                last_digit = int(cnic.split("-")[-1])
                gender_signal = extracted.get("gender")
                
                if gender_signal in ["Male", "Female"]:
                    is_male_digit = (last_digit % 2 != 0)
                    is_expected_gender = (gender_signal == "Male" and is_male_digit) or (gender_signal == "Female" and not is_male_digit)
                    checks["cnic_check_digit_valid"] = is_expected_gender
                    if not is_expected_gender:
                        failures.append(f"Biometric structural designation anomaly: Last digit code '{last_digit}' conflicts with identified profile configuration.")
                else:
                    checks["cnic_check_digit_valid"] = False

        name = extracted.get("name_english")
        checks["name_present"] = bool(name and len(str(name).strip()) > 2)
        if not checks["name_present"]:
            failures.append("Identity profile text string content parsing value extraction rejected.")

        dob_ok, dob_err = self._check_date(
            extracted.get("date_of_birth"), "Date of birth",
            min_year=1900, max_year=date.today().year - 15
        )
        checks["dob_valid"] = dob_ok
        if not dob_ok: failures.append(dob_err)

        exp_str = extracted.get("date_of_expiry")
        if exp_str and str(exp_str).strip().lower() in ("lifetime", "حیات"):
            # "Lifetime" CNICs (typically issued to citizens 60+) never expire.
            extracted["date_of_expiry"] = "Lifetime"
            checks["expiry_valid"] = True
            checks["not_expired"] = True
        else:
            exp_ok, exp_err = self._check_date(exp_str, "Expiry date", min_year=2000, max_year=2060)
            checks["expiry_valid"] = exp_ok
            if not exp_ok: failures.append(exp_err)

            if exp_ok and exp_str:
                try:
                    d, m, y = map(int, exp_str.split("/"))
                    expired = date(y, m, d) < date.today()
                    checks["not_expired"] = not expired
                    if expired:
                        failures.append(f"Document structural validity timeframe expired on calendar context footprint target: {exp_str}")
                except Exception:
                    checks["not_expired"] = None

        anomalies     = observations.get("anomalies", [])
        is_photocopy  = observations.get("is_color_photocopy") or observations.get("is_black_white_photocopy")
        is_screenshot = observations.get("is_screenshot")

        checks["no_anomalies"]   = len(anomalies) == 0
        checks["not_photocopy"]  = not is_photocopy
        checks["not_screenshot"] = not is_screenshot

        if anomalies:     failures.append(f"Structural target surface anomalies flagged: {', '.join(anomalies)}")
        if is_photocopy:  failures.append("Input surface characteristic profile matches photocopy properties — original verification file required.")
        if is_screenshot: failures.append("Input capture mechanism characteristics map to software screenshots — live verification asset signature required.")

        checks["gender_valid"] = extracted.get("gender") in ("Male", "Female")

        if has_back or extracted.get("province"):
            valid_provinces = {"Punjab", "Sindh", "KPK", "Balochistan", "Gilgit-Baltistan", "AJK", "ICT"}
            province = extracted.get("province")
            checks["province_valid"] = province in valid_provinces if province else False
            if not checks["province_valid"]:
                failures.append(f"Geographic provincial parameter target parsing out of domain constraints: '{province}'")

        hard = {"cnic_present", "cnic_format_valid", "not_expired", "name_present", "not_photocopy"}
        hard_failed = [k for k, v in checks.items() if v is False and k in hard]
        verdict = "REJECTED" if hard_failed else ("REVIEW" if failures else "VERIFIED")

        return {
            "verdict":      verdict,
            "checks":       checks,
            "failures":     failures,
            "total_checks": len(checks),
            "passed":       sum(1 for v in checks.values() if v is True),
            "failed":       sum(1 for v in checks.values() if v is False),
        }

    @staticmethod
    def _check_date(date_str, label, min_year, max_year):
        if not date_str:
            return False, f"Missing system parameter asset: {label}"
        try:
            parts = date_str.split("/")
            if len(parts) != 3: raise ValueError
            d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
            if not (1 <= m <= 12 and 1 <= d <= 31): raise ValueError
            if not (min_year <= y <= max_year):
                return False, f"Calendar target parameters for {label} exit acceptable validation ranges at marker point: {y}"
            return True, None
        except Exception:
            return False, f"Date format conversion constraint failure for {label}: '{date_str}' — system requires explicit matching format sequence: DD/MM/YYYY"