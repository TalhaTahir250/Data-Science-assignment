import logging
from pathlib import Path
from datetime import datetime

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse

from app.core.config import settings
from app.api.v1 import sessions
from app.core.database import init_db

logger = logging.getLogger("PakVerify")

# --- BOOT UP THE DATABASE ---
init_db()

app = FastAPI(title=settings.APP_NAME, version=settings.VERSION)

# ── CORS Middleware (Crucial for frontend communication) ──
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], 
    allow_credentials=True,
    allow_methods=["*"], 
    allow_headers=["*"],
)

# ── Include your Enterprise API Router ──
app.include_router(sessions.router, prefix="/v1/sessions", tags=["v1: Enterprise Session Flow"])

# ── Mount the Static Directory ──
static_dir = Path(__file__).parent.parent / "static"
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

# ── Serve the UI ──
@app.get("/", include_in_schema=False)
async def serve_ui():
    """Serves the interactive camera UI when visiting the root URL."""
    index_file = static_dir / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return HTMLResponse(content="<h1>PakVerify API is Running (UI not found in /static)</h1>", status_code=200)

# ── Health Check ──
@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": settings.VERSION,
        "time": datetime.utcnow().isoformat(),
    }