import requests
import json

# Define your endpoint and the secret API key provided to the client
API_URL = "http://127.0.0.1:8000/verify/"  # Change to your cloud URL in production
API_KEY = "pakverify-v01-key"

# Path to the local assets the client wants to verify
CNIC_FRONT_PATH = "path/to/cnic_front.jpg"
SELFIE_PATH = "path/to/selfie.jpg"
CNIC_BACK_PATH = None  # Optional field

print("Connecting to PakVerify secure identity gateway...")

try:
    # Open the image binary streams
    with open(CNIC_FRONT_PATH, "rb") as front_img, open(SELFIE_PATH, "rb") as selfie_img:
        
        # Prepare multipart form-data payload
        files = {
            "front": ("front.jpg", front_img, "image/jpeg"),
            "selfie": ("selfie.jpg", selfie_img, "image/jpeg")
        }
        
        if CNIC_BACK_PATH:
            back_img = open(CNIC_BACK_PATH, "rb")
            files["back"] = ("back.jpg", back_img, "image/jpeg")

        # Set the mandatory tenant authentication header
        headers = {
            "X-API-Key": API_KEY
        }

        # Dispatch the request to the processing cluster
        response = requests.post(API_URL, headers=headers, files=files)

    # Parse and interpret results
    if response.status_code == 200:
        result = response.json()
        print("\n=== VERIFICATION TRANSACTION COMPLETED ===")
        print(f"Status Verdict : {result['status']}")
        print(f"Match Success  : {result['verified']}")
        print(f"Confidence     : {json.dumps(result['confidence'], indent=2)}")
        print(f"Extracted Data : {json.dumps(result['extracted'], indent=2)}")
    elif response.status_code == 429:
        print("\n[!] Error: Rate limit exceeded. Please back off and retry in a moment.")
    else:
        print(f"\n[!] Verification Gateway Failed [{response.status_code}]: {response.text}")

except FileNotFoundError as e:
    print(f"Local Resource Configuration Error: {e}")
except Exception as e:
    print(f"Network Connection Transmission Error: {e}")