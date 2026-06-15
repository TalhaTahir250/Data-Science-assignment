"""
examples/test_session_flow.py

End-to-end smoke test for the v0.2 session-based verification flow.
Run this against a live server (e.g. `uvicorn app.main:app --reload`)
after running `python scripts/seed_demo_orgs.py`.

Usage:
    python examples/test_session_flow.py \\
        --front path/to/cnic_front.jpg \\
        --back path/to/cnic_back.jpg \\
        --selfie path/to/selfie.jpg \\
        [--api-key demo-growth-key] \\
        [--base-url http://127.0.0.1:8000]

If --back/--selfie are omitted, the script stops after whichever steps it
can run and reports the session status.

To see the full flow including the webhook, also run in another terminal:
    python examples/webhook_receiver.py
"""

import argparse
import time
import sys

import httpx


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--front", required=True, help="Path to CNIC front image")
    parser.add_argument("--back", help="Path to CNIC back image")
    parser.add_argument("--selfie", help="Path to live selfie image")
    parser.add_argument("--api-key", default="demo-growth-key")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()

    headers = {"X-API-Key": args.api_key}
    client = httpx.Client(base_url=args.base_url, headers=headers, timeout=60.0)

    # 1. Initiate session
    print(">> POST /v1/sessions")
    resp = client.post("/v1/sessions/")
    print(resp.status_code, resp.json())
    if resp.status_code == 402:
        print("Session blocked by billing engine (expected for inactive/over-quota orgs).")
        sys.exit(0)
    resp.raise_for_status()
    session_id = resp.json()["session_id"]
    print(f"session_id = {session_id}\n")

    # 2. Front capture
    print(">> POST /v1/sessions/{id}/document/front")
    with open(args.front, "rb") as f:
        resp = client.post(f"/v1/sessions/{session_id}/document/front",
                            files={"image": f})
    print(resp.status_code, {k: v for k, v in resp.json().items() if k != "extracted"})
    front_result = resp.json()
    print(f"front passed={front_result['passed']}, state={front_result['state']}\n")

    if not args.back:
        print("No --back provided; stopping here.")
        return

    # 3. Back capture
    print(">> POST /v1/sessions/{id}/document/back")
    with open(args.back, "rb") as f:
        resp = client.post(f"/v1/sessions/{session_id}/document/back",
                            files={"image": f})
    back_result = resp.json()
    print(resp.status_code, {k: v for k, v in back_result.items() if k != "extracted"})
    print(f"back passed={back_result['passed']}, state={back_result['state']}\n")

    if not args.selfie:
        print("No --selfie provided; stopping here.")
        return

    # 4. Biometrics (async)
    print(">> POST /v1/sessions/{id}/biometrics")
    with open(args.selfie, "rb") as f:
        resp = client.post(f"/v1/sessions/{session_id}/biometrics",
                            files={"selfie": f})
    print(resp.status_code, resp.json(), "\n")

    # 5. Poll for terminal state
    print(">> Polling GET /v1/sessions/{id} ...")
    for _ in range(30):
        resp = client.get(f"/v1/sessions/{session_id}")
        status = resp.json()
        print(f"  state = {status['state']}")
        if status["state"] in ("VERIFIED", "REJECTED", "SPOOF_DETECTED"):
            print("\nFinal session status:")
            print(status)
            break
        time.sleep(1)
    else:
        print("Timed out waiting for a terminal state.")


if __name__ == "__main__":
    main()
