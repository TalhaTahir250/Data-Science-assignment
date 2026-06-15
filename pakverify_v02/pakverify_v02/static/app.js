/**
 * static/app.js
 * FSM UI Orchestration, Client-Side Compression, and Async Polling Engine.
 */

let currentSessionId = null;
let currentStep = "init"; 
let streamReference = null;

const UI = {
    indicator: document.getElementById("step-indicator"),
    screens: {
        init: document.getElementById("init-screen"),
        camera: document.getElementById("camera-screen"),
        processing: document.getElementById("processing-screen"),
        results: document.getElementById("results-screen")
    },
    video: document.getElementById("webcam"),
    overlay: document.getElementById("camera-overlay"),
    canvas: document.getElementById("compression-canvas"),
    pollingText: document.getElementById("polling-text"),
    banner: document.getElementById("result-status-banner"),
    dataList: document.getElementById("extracted-data-list"),
    biometricText: document.getElementById("biometric-match-text")
};

// Event Listeners
document.getElementById("start-session-btn").addEventListener("click", startSession);
document.getElementById("capture-btn").addEventListener("click", handleCapture);
document.getElementById("restart-btn").addEventListener("click", () => window.location.reload());

function switchScreen(screenName) {
    Object.keys(UI.screens).forEach(key => {
        UI.screens[key].classList.remove("active");
    });
    UI.screens[screenName].classList.add("active");
}

async function startSession() {
    try {
        // Step 1: Initialize Session on Backend
        const response = await fetch("/v1/sessions/", {
            method: "POST",
            headers: { 
                "Content-Type": "application/json", 
                "X-API-Key": "demo-payg-key" 
            }
        });
        
        if (!response.ok) {
            throw new Error("API Key or routing mismatch");
        }
        
        const data = await response.json();
        currentSessionId = data.session_id;

        // Step 2: Trigger Camera View
        currentStep = "front";
        UI.indicator.innerText = "Step 2: Capture Front of CNIC Card";
        UI.overlay.className = "overlay-guide card-guide";
        switchScreen("camera");
        await startCamera();
    } catch (err) {
        alert("Failed to initialize verification session. Please check the console for details.");
        console.error(err);
    }
}

async function startCamera() {
    if (streamReference) {
        streamReference.getTracks().forEach(track => track.stop());
    }
    try {
        streamReference = await navigator.mediaDevices.getUserMedia({
            // FIXED: Changed "environment" to "user" to force the laptop webcam
            video: { width: { ideal: 1920 }, height: { ideal: 1080 }, facingMode: "user" },
            audio: false
        });
        UI.video.srcObject = streamReference;
    } catch (err) {
        alert("Camera access denied. Please allow camera permissions in your browser address bar.");
    }
}

function stopCamera() {
    if (streamReference) {
        streamReference.getTracks().forEach(track => track.stop());
        streamReference = null;
    }
}

async function handleCapture() {
    const ctx = UI.canvas.getContext("2d");
    
    let targetWidth = UI.video.videoWidth;
    let targetHeight = UI.video.videoHeight;
    const maxDimension = 1920;

    if (targetWidth > maxDimension || targetHeight > maxDimension) {
        if (targetWidth > targetHeight) {
            targetHeight = Math.round((targetHeight * maxDimension) / targetWidth);
            targetWidth = maxDimension;
        } else {
            targetWidth = Math.round((targetWidth * maxDimension) / targetHeight);
            targetHeight = maxDimension;
        }
    }

    UI.canvas.width = targetWidth;
    UI.canvas.height = targetHeight;
    
    ctx.drawImage(UI.video, 0, 0, targetWidth, targetHeight);
    
    UI.canvas.toBlob(async (blob) => {
        await uploadCapturedFrame(blob);
    }, "image/jpeg", 0.82);
}

async function uploadCapturedFrame(imageBlob) {
    const formData = new FormData();
    formData.append("file", imageBlob, `${currentStep}.jpg`);

    let endpoint = "";
    if (currentStep === "front") endpoint = `/v1/sessions/${currentSessionId}/document/front`;
    else if (currentStep === "back") endpoint = `/v1/sessions/${currentSessionId}/document/back`;
    else if (currentStep === "selfie") endpoint = `/v1/sessions/${currentSessionId}/biometrics`;

    try {
        stopCamera();
        switchScreen("processing");
        UI.pollingText.innerText = `Uploading and parsing raw ${currentStep} secure payload...`;

        const response = await fetch(endpoint, {
            method: "POST",
            headers: { "X-API-Key": "demo-payg-key" },
            body: formData
        });

        if (!response.ok) throw new Error("Upload processing failure.");

        if (currentStep === "front") {
            currentStep = "back";
            UI.indicator.innerText = "Step 3: Capture Back of CNIC Card";
            UI.overlay.className = "overlay-guide card-guide";
            switchScreen("camera");
            await startCamera();
        } else if (currentStep === "back") {
            currentStep = "selfie";
            UI.indicator.innerText = "Step 4: Take live selfie matching verification context";
            UI.overlay.className = "overlay-guide face-guide";
            switchScreen("camera");
            await startCamera();
        } else if (currentStep === "selfie") {
            currentStep = "processing";
            UI.indicator.innerText = "Step 5: Processing System Evaluation";
            startAsynchronousPolling();
        }
    } catch (err) {
        alert(`Error on step: ${currentStep}. Make sure your backend server is running.`);
        await startCamera();
        switchScreen("camera");
    }
}

function startAsynchronousPolling() {
    UI.pollingText.innerText = "Biometrics accepted into pipeline queue. Awaiting multi-model response validation...";
    
    const pollInterval = setInterval(async () => {
        try {
            const response = await fetch(`/v1/sessions/${currentSessionId}`, {
                headers: { "X-API-Key": "demo-payg-key" }
            });
            const session = await response.json();
            const state = session.state;

            UI.pollingText.innerText = `Analyzing models... Status: ${state}`;

            if (state === "VERIFIED" || state === "REJECTED" || state === "SPOOF_DETECTED") {
                clearInterval(pollInterval);
                renderDashboardResults(session);
            }
        } catch (err) {
            console.error("Polling validation frame missed. Re-establishing link...");
        }
    }, 2500);
}

function renderDashboardResults(session) {
    switchScreen("results");
    UI.indicator.innerText = "Verification Session Finalized";

    let extracted = {};
    try {
        const parsed = JSON.parse(session.extracted_data);
        extracted = parsed.extracted || parsed;
    } catch(e) {
        extracted = {};
    }

    let bio = {};
    try {
        bio = JSON.parse(session.biometric_result) || {};
    } catch(e) {}

    if (session.state === "VERIFIED") {
        UI.banner.className = "status-banner success";
        UI.banner.innerText = "IDENTITY VERIFIED SUCCESSFUL";
    } else {
        UI.banner.className = "status-banner fail";
        UI.banner.innerText = `IDENTITY CHECK FAILED: ${session.state}`;
    }

    UI.dataList.innerHTML = `
        <li><strong>Full Name:</strong> ${extracted.name_english || "Not Detected"}</li>
        <li><strong>Urdu Name:</strong> ${extracted.name_urdu || "Not Detected"}</li>
        <li><strong>CNIC Identification Number:</strong> ${session.cnic_number || extracted.cnic_number || "Not Detected"}</li>
        <li><strong>Date of Birth:</strong> ${extracted.date_of_birth || "Not Detected"}</li>
        <li><strong>Expiry Date:</strong> ${extracted.expiry_date || "Not Detected"}</li>
    `;

    if (session.state === "VERIFIED") {
        UI.biometricText.innerText = `Facial analysis confirms live identity match. Confidence metrics match verification specifications (Distance score: ${bio.distance ? bio.distance.toFixed(3) : "Within parameters"}).`;
    } else if (session.state === "SPOOF_DETECTED") {
        UI.biometricText.innerText = "Critical Flag Raised: Presentation attack or spoofing artifact detected on live capture feed.";
    } else {
        UI.biometricText.innerText = "Verification assessment mismatch: Facial architecture signatures do not align with identification card image metrics.";
    }
}