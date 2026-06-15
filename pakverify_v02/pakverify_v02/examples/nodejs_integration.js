const axios = require('axios');
const FormData = require('form-data');
const fs = require('fs');

// Define connection profile
const API_URL = 'http://127.0.0.1:8000/verify/'; // Change to cloud URL in production
const API_KEY = 'pakverify-v01-key';

// Asset resource locations
const CNIC_FRONT_PATH = 'path/to/cnic_front.jpg';
const SELFIE_PATH = 'path/to/selfie.jpg';

async function executeIdentityScan() {
    console.log("Initializing digital KYC sequence via PakVerify...");

    const form = new FormData();
    
    try {
        // Append visual telemetry arrays to stream
        form.append('front', fs.createReadStream(CNIC_FRONT_PATH));
        form.append('selfie', fs.createReadStream(SELFIE_PATH));

        // Submit to API endpoint with target authorization vectors
        const response = await axios.post(API_URL, form, {
            headers: {
                ...form.getHeaders(),
                'X-API-Key': API_KEY
            }
        });

        console.log("\n=== VERIFICATION TRANSACTION COMPLETED ===");
        console.log(`Status Verdict : ${response.data.status}`);
        console.log(`Match Success  : ${response.data.verified}`);
        console.log("Extracted Data :", JSON.stringify(response.data.extracted, null, 2));

    } catch (error) {
        if (error.response) {
            if (error.response.status === 429) {
                console.error("\n[!] Error: Rate limit triggered. Maximum minute allocation exceeded.");
            } else {
                console.error(`\n[!] Remote Engine Exception [${error.response.status}]:`, error.response.data);
            }
        } else {
            console.error("\n[!] Local Network Layer Exception:", error.message);
        }
    }
}

executeIdentityScan();