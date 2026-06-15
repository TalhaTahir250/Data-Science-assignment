# PAK_VERIFY

## v0.1 — Single-shot verification (legacy, still live at `/verify`)

**What this does:**
Upload a CNIC image → Gemini 1.5 Pro reads it → Returns all fields + full validation

**Flow:**
```
Upload → Gemini Vision → JSON extraction → 9 validation checks → Verdict
```

---

## Setup (3 steps)

**Step 1 — Install**
```bash
pip install -r requirements.txt
```

**Step 2 — Configure**
```bash
cp .env.example .env
# Open .env and add your Gemini API key:
# Get it free from: https://aistudio.google.com/app/apikey
GEMINI_API_KEY=your-key-here
```

**Step 3 — Run**
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Open browser: **http://localhost:8000**

---

## Expose publicly (for demos / mobile testing)

```bash
# Install ngrok from ngrok.com, then:
ngrok http 8000
# Paste the https://xxx.ngrok-free.app URL into the dashboard Base URL field
```

---

## API Usage

**Endpoint:** `POST /verify`

```bash
curl -X POST "http://localhost:8000/verify" \
  -H "X-API-Key: pakverify-v01-key" \
  -F "image=@cnic_front.jpg"
```

**Response:**
```json
{
  "status": "VERIFIED",
  "verified": true,
  "extracted": {
    "cnic_number": "35202-1234567-9",
    "name_english": "Muhammad Ali Khan",
    "father_name_english": "Abdul Rehman Khan",
    "date_of_birth": "01/01/1990",
    "date_of_expiry": "15/06/2030",
    "gender": "Male",
    "address": "House 5, Street 3, Model Town, Lahore",
    "city": "Lahore",
    "district": "Lahore",
    "province": "Punjab"
  },
  "confidence": {
    "overall": 0.95,
    "cnic_number": 0.98
  },
  "validation": {
    "verdict": "VERIFIED",
    "passed_checks": 9,
    "failed_checks": 0,
    "failures": []
  }
}
```

---

## What v0.1 validates

| Check | What it tests |
|---|---|
| CNIC Present | Number was extracted from image |
| CNIC Format | Matches XXXXX-XXXXXXX-X pattern |
| District Code | First 5 digits are a real Pakistani district (10000–55000) |
| Check Digit | Last digit mathematically matches the NADRA formula |
| Name Present | Full name was readable |
| DOB Valid | Date of birth is in DD/MM/YYYY and plausible |
| Expiry Valid | Expiry date is present and valid format |
| Not Expired | Expiry date is in the future |
| Not Photocopy | Gemini confirms image is an original photo |
| Not Screenshot | Not a screen capture of a digital CNIC |
| No Anomalies | No editing marks or inconsistencies detected |

---

## What's NOT in v0.1 (now built in v0.2, see below)

- NADRA live database lookup (still v0.3, future)
- AML / sanctions screening (still v0.4, future)
- Admin dashboard (future)

---

# v0.2 — Session-based multi-tenant verification flow

v0.2 adds a sequential, stateful verification flow on top of v0.1's engine
(same OCR + DeepFace under the hood), plus a multi-tenant billing layer,
async biometrics with signed webhooks, and ephemeral storage with hard
deletion. v0.1's `/verify` endpoint keeps working unchanged.

## Setup

Same install/configure steps as v0.1 (`pip install -r requirements.txt`,
copy `.env.example` to `.env`, add your `GEMINI_API_KEY`). The database
schema is migrated automatically on startup — your existing `pakverify.db`
and test client are preserved and upgraded in place.

To exercise the multi-tenant billing tiers and webhooks, seed some demo
organizations first:

```bash
python scripts/seed_demo_orgs.py
```

This creates four demo orgs:

| API Key | Tier | Behavior |
|---|---|---|
| `demo-payg-key` | PAY_AS_YOU_GO | Always allowed if active |
| `demo-growth-key` | GROWTH | Sends webhooks to local receiver |
| `demo-enterprise-key` | ENTERPRISE | `monthly_quota=1` — second session in a billing cycle gets HTTP 402 |
| `demo-suspended-key` | PAY_AS_YOU_GO | `is_active=0` — every session init returns HTTP 402 |

## The flow

```
POST /v1/sessions                          -> session_id, state=INITIATED
POST /v1/sessions/{id}/document/front      -> OCR front side; state=FRONT_COMPLETED if it passes
POST /v1/sessions/{id}/document/back       -> OCR back side + Urdu fields; state=BACK_COMPLETED if it passes
POST /v1/sessions/{id}/biometrics          -> 202 Accepted; runs DeepFace async; webhook fires on completion
GET  /v1/sessions/{id}                     -> poll current state/result at any time
```

State machine:

```
INITIATED -> FRONT_COMPLETED -> BACK_COMPLETED -> PROCESSING_BIOMETRICS -> {VERIFIED | REJECTED | SPOOF_DETECTED}
```

If a capture step fails its quality/validation checks (blurry image, card
not detected, hard validation failures), the session stays in its current
state and the response has `"passed": false` — the client just retries the
same step with a new image. State only advances on a pass.

## Quick test

Terminal 1 — start the server:
```bash
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

Terminal 2 — start the local webhook receiver (verifies HMAC signatures):
```bash
python examples/webhook_receiver.py
```

Terminal 3 — run the end-to-end flow:
```bash
python examples/test_session_flow.py \
  --front cnic_front.jpg \
  --back cnic_back.jpg \
  --selfie selfie.jpg \
  --api-key demo-growth-key
```

You should see the session step through each state, terminal 2 print the
received webhook payload with `Signature verified against known secrets: True`,
and the session's image files (`app/storage/sessions/<id>/`) disappear once
the terminal state is reached.

## Manual curl walkthrough

```bash
# 1. Initiate
curl -s -X POST http://localhost:8000/v1/sessions/ \
  -H "X-API-Key: demo-growth-key" | tee /tmp/session.json

SESSION_ID=$(python3 -c "import json; print(json.load(open('/tmp/session.json'))['session_id'])")

# 2. Front capture
curl -s -X POST "http://localhost:8000/v1/sessions/$SESSION_ID/document/front" \
  -H "X-API-Key: demo-growth-key" -F "image=@cnic_front.jpg"

# 3. Back capture
curl -s -X POST "http://localhost:8000/v1/sessions/$SESSION_ID/document/back" \
  -H "X-API-Key: demo-growth-key" -F "image=@cnic_back.jpg"

# 4. Biometrics (returns 202 immediately)
curl -s -X POST "http://localhost:8000/v1/sessions/$SESSION_ID/biometrics" \
  -H "X-API-Key: demo-growth-key" -F "selfie=@selfie.jpg"

# 5. Poll status
curl -s "http://localhost:8000/v1/sessions/$SESSION_ID" -H "X-API-Key: demo-growth-key"
```

## Billing engine

Configured per-org on the `clients` table: `pricing_tier`, `monthly_quota`,
`monthly_usage_counter`, `billing_cycle_start`, `is_active`.

- `monthly_usage_counter` increments only when a session reaches a terminal
  state (`VERIFIED`, `REJECTED`, `SPOOF_DETECTED`). Abandoned/failed capture
  steps don't count.
- `POST /v1/sessions` returns **HTTP 402** if `is_active=0`, or if
  `monthly_quota > 0` and `monthly_usage_counter >= monthly_quota`.
- `monthly_quota=0` means no hard cap (PAY_AS_YOU_GO default, or GROWTH with
  unlimited billed overage).
- Billing cycles auto-reset monthly based on `billing_cycle_start`.

This is metering + enforcement, not a full invoicing system — `estimate_cost_usd()`
in `app/utils/billing.py` gives a rough per-cycle cost for reporting.

## Webhooks

On reaching a terminal state, the backend POSTs a JSON payload to the org's
`webhook_url`, signed with `webhook_secret` via the `X-PakVerify-Signature`
header (hex HMAC-SHA256 over the raw JSON body). See
`app/utils/webhooks.py` for the signing function and a verification snippet
your B2B customers can use on their end.

## Ephemeral storage

Uploaded images live in `app/storage/sessions/<session_id>/` only for the
duration of the session. The instant a session reaches a terminal state and
the webhook dispatch attempt completes (success or retries exhausted), the
entire session directory is deleted (`app/services/session_store.py:hard_delete_session_files`).
No face images or ID photos persist after that point.

## What's still NOT in v0.2

- Camera-driven frontend (card silhouette overlays, oval face guide,
  auto-capture-when-steady) — this is a separate mobile/web UX project
- Real cloud object storage (S3/GCS) — currently local temp + guaranteed deletion
- Webhook dead-letter queue / persistent retry beyond immediate attempts
- Enterprise tier admin config UI
- Tier-specific overage invoicing (proration, billing cycle edge cases)
