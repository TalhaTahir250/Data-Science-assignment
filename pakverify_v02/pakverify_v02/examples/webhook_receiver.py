"""
examples/webhook_receiver.py

A minimal local HTTP server that receives PakVerify webhooks and verifies
the X-PakVerify-Signature HMAC-SHA256 header. Use this to test the async
webhook dispatch in app/utils/webhooks.py end-to-end.

Usage:
    python examples/webhook_receiver.py
    # listens on http://127.0.0.1:9000/webhooks/pakverify

Pair with scripts/seed_demo_orgs.py — the "Demo Lender (Growth)" and
"Demo Bank (Enterprise)" orgs are pre-configured to send webhooks here
with secrets "growth-webhook-secret" and "enterprise-webhook-secret".
"""

import hmac
import hashlib
import json
from http.server import BaseHTTPRequestHandler, HTTPServer

# Map of webhook_secret values you've configured per org (for local testing
# only — in production each org's secret is looked up server-side).
KNOWN_SECRETS = {
    "growth-webhook-secret",
    "enterprise-webhook-secret",
}

SIGNATURE_HEADER = "X-PakVerify-Signature"


class WebhookHandler(BaseHTTPRequestHandler):
    def do_POST(self):
        length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(length)
        signature = self.headers.get(SIGNATURE_HEADER, "")

        verified = False
        for secret in KNOWN_SECRETS:
            expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
            if hmac.compare_digest(expected, signature):
                verified = True
                break

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            payload = {}

        print("=" * 60)
        print(f"Received webhook for path: {self.path}")
        print(f"Signature header present: {bool(signature)}")
        print(f"Signature verified against known secrets: {verified}")
        print(json.dumps(payload, indent=2))
        print("=" * 60)

        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"received": true}')

    def log_message(self, format, *args):
        pass  # keep stdout clean; we print our own summary above


if __name__ == "__main__":
    server = HTTPServer(("127.0.0.1", 9000), WebhookHandler)
    print("Webhook receiver listening on http://127.0.0.1:9000/webhooks/pakverify")
    server.serve_forever()
