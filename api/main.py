"""
api/main.py
Main FastAPI app. All routers mounted here.
Run: uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload

Fixes applied:
  - CORS restricted to Railway URL + localhost (not open to all)
  - Scheduler with error logging (jobs won't silently fail)
  - /health endpoint shows DB + scheduler status
  - Startup/shutdown logs for easier debugging
"""

import logging
import os
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.events import EVENT_JOB_ERROR, EVENT_JOB_EXECUTED
from booking.booking_handler import send_appointment_reminders
from config import get_anthropic_api_key

# ── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aaa-hvac")

# ── Scheduler ─────────────────────────────────────────────────────────────────
scheduler = AsyncIOScheduler()


def scheduler_listener(event):
    """Log scheduler job success or failure."""
    if event.exception:
        logger.error(f"[SCHEDULER] Job {event.job_id} FAILED: {event.exception}")
    else:
        logger.info(f"[SCHEDULER] Job {event.job_id} completed successfully")


# ── CORS origins ──────────────────────────────────────────────────────────────
def get_allowed_origins() -> list[str]:
    """Build CORS whitelist from environment — never allow * in production."""
    origins = [
        "http://localhost:8000",
        "http://localhost:8501",  # Streamlit local
    ]
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if railway_domain:
        origins.append(f"https://{railway_domain}")

    # Allow extra origins via env var (comma-separated)
    extra = os.getenv("CORS_ALLOWED_ORIGINS", "")
    if extra:
        origins.extend([o.strip() for o in extra.split(",") if o.strip()])

    return origins


# ── Lifespan ──────────────────────────────────────────────────────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    anthropic_key_loaded = False
    try:
        _ = get_anthropic_api_key()
        anthropic_key_loaded = True
    except Exception:
        anthropic_key_loaded = False

    logger.info(f"[MAIN] Anthropic key loaded: {str(anthropic_key_loaded).lower()}")

    scheduler.add_listener(scheduler_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)
    scheduler.add_job(
        send_appointment_reminders,
        "interval",
        minutes=30,
        id="appointment_reminders",
        replace_existing=True,
        misfire_grace_time=60,
    )
    scheduler.start()
    logger.info("[MAIN] Scheduler started — appointment reminders every 30 min")
    logger.info(f"[MAIN] CORS allowed origins: {get_allowed_origins()}")

    yield

    # Shutdown
    scheduler.shutdown(wait=False)
    logger.info("[MAIN] Scheduler stopped")


# ── App ───────────────────────────────────────────────────────────────────────
app = FastAPI(
    title="AAA HVAC - AI Automation API",
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def serve_dashboard():
    return FileResponse("static/hvac-demo.html")

app.add_middleware(
    CORSMiddleware,
    allow_origins=get_allowed_origins(),
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE"],
    allow_headers=["*"],
)

# ── Routers ───────────────────────────────────────────────────────────────────
from missed_call.missed_call_handler import router as missed_call_router
from booking.booking_handler import router as booking_router
from speed_to_lead.speed_to_lead import router as speed_router
from voice_ai.vapi_handler import router as vapi_router
from api.onboarding import router as onboarding_router

app.include_router(missed_call_router, prefix="/twilio",     tags=["Missed Call"])
app.include_router(booking_router,     prefix="/booking",    tags=["Booking"])
app.include_router(speed_router,       prefix="/lead",       tags=["Speed To Lead"])
app.include_router(vapi_router,        prefix="/vapi",        tags=["Voice AI"])
app.include_router(onboarding_router,  prefix="/onboarding", tags=["Onboarding"])


@app.get("/privacy-policy")
async def privacy_policy():
    return HTMLResponse(content="""
    <html>
        <head><title>Privacy Policy - Globiz LLC (Midvio)</title></head>
        <body>
            <h1>Privacy Policy for SMS Messaging</h1>
            <p><strong>Company:</strong> Globiz LLC (Midvio)</p>
            <p><strong>Contact:</strong> support@midvio.com</p>
            <p><strong>SMS opt-out:</strong> Reply STOP to unsubscribe</p>
            <p><strong>Last updated:</strong> April 24, 2026</p>
        </body>
    </html>
    """)


@app.get("/terms")
async def terms():
    return HTMLResponse(content="""
    <html>
        <head><title>Terms of Service - Globiz LLC (Midvio)</title></head>
        <body>
            <h1>Terms of Service for SMS Messaging</h1>
            <p><strong>Company:</strong> Globiz LLC (Midvio)</p>
            <p><strong>Contact:</strong> support@midvio.com</p>
            <p><strong>SMS opt-out:</strong> Reply STOP to unsubscribe</p>
            <p><strong>Last updated:</strong> April 24, 2026</p>
        </body>
    </html>
    """)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
async def health():
    """Detailed health check — DB connection + scheduler status."""
    db_ok = False
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception as e:
        logger.warning(f"[HEALTH] DB check failed: {e}")

    return {
        "status": "ok" if db_ok else "degraded",
        "service": "AAA HVAC AI",
        "version": "1.0.0",
        "database": "connected" if db_ok else "unreachable",
        "scheduler": "running" if scheduler.running else "stopped",
        "environment": os.getenv("RAILWAY_ENVIRONMENT", "local"),
    }