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
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
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
        "https://dashboard.midvio.com",
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
async def serve_root():
    if os.getenv("SERVE_DASHBOARD") == "true":
        return FileResponse("static/dashboard.html")
    return FileResponse("static/hvac-demo.html")


@app.get("/dashboard")
async def serve_admin_dashboard():
    return FileResponse("static/dashboard.html")
    
@app.get("/client")
async def serve_client_dashboard():
    return FileResponse("static/client-dashboard.html")

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


class DashboardLoginRequest(BaseModel):
    username: str
    password: str


@app.post("/auth/dashboard")
async def dashboard_login(req: DashboardLoginRequest):
    """Validate admin credentials against DASHBOARD_ADMIN_USER / DASHBOARD_ADMIN_PASS."""
    expected_user = os.getenv("DASHBOARD_ADMIN_USER", "")
    expected_pass = os.getenv("DASHBOARD_ADMIN_PASS", "")
    if not expected_user or not expected_pass:
        raise HTTPException(
            status_code=503,
            detail="Dashboard authentication is not configured on the server.",
        )
    if req.username == expected_user and req.password == expected_pass:
        return {"ok": True, "role": "admin"}
    raise HTTPException(status_code=401, detail="Invalid username or password.")


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

@app.get("/leads")
async def get_leads():
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, phone, status, created_at, name FROM leads ORDER BY created_at DESC")
                rows = cursor.fetchall()
                return {"leads": [{"id":r[0],"phone":r[1],"status":r[2],"created_at":str(r[3]),"name":r[4]} for r in rows]}
    except Exception as e:
        return {"leads": [], "error": str(e)}


@app.get("/appointments")
async def get_appointments(client_id: int = None):
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                if client_id:
                    cursor.execute("SELECT id, name, address, appointment_time, appointment_date, service_type, status FROM appointments WHERE client_id = %s ORDER BY created_at DESC", (client_id,))
                else:
                    cursor.execute("SELECT id, name, address, appointment_time, appointment_date, service_type, status FROM appointments ORDER BY created_at DESC")
                rows = cursor.fetchall()
                return {"appointments": [{"id":r[0],"name":r[1],"address":r[2],"time":r[3],"date":r[4],"type":r[5],"status":r[6]} for r in rows]}
    except Exception as e:
        return {"appointments": [], "error": str(e)}

@app.get("/voice-calls")
async def get_voice_calls(client_id: int = None):
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                if client_id:
                    cursor.execute("SELECT id, caller_name, phone, call_type, duration, status, created_at FROM voice_calls WHERE client_id = %s ORDER BY created_at DESC", (client_id,))
                else:
                    cursor.execute("SELECT id, caller_name, phone, call_type, duration, status, created_at FROM voice_calls ORDER BY created_at DESC")
                rows = cursor.fetchall()
                return {"calls": [{"id":r[0],"name":r[1],"phone":r[2],"type":r[3],"duration":r[4],"status":r[5],"date":str(r[6])} for r in rows]}
    except Exception as e:
        return {"calls": [], "error": str(e)}

@app.post("/clients")
async def create_client(data: dict):
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "INSERT INTO clients (company_name, username, password_hash, phone_number, active) VALUES (%s, %s, %s, %s, %s)",
                    (data['company_name'], data['username'], data['password'], data.get('phone_number',''), True)
                )
                conn.commit()
                return {"ok": True}
    except Exception as e:
        return {"ok": False, "detail": str(e)}

@app.get("/clients")
async def get_clients():
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute("SELECT id, company_name, username, phone_number, active FROM clients ORDER BY company_name")
                rows = cursor.fetchall()
                return {"clients": [{"id":r[0],"company_name":r[1],"username":r[2],"phone_number":r[3],"active":r[4]} for r in rows]}
    except Exception as e:
        return {"clients": [], "error": str(e)}
@app.post("/client-login")
async def client_login(data: dict):
    import hashlib
    try:
        from db.postgres import get_conn
        hashed = hashlib.sha256(data['password'].encode()).hexdigest()
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    "SELECT id, company_name, username, active FROM clients WHERE username = %s AND password_hash = %s",
                    (data['username'], hashed)
                )
                row = cursor.fetchone()
                if row and row[3]:
                    return {"ok": True, "client_id": row[0], "company_name": row[1], "username": row[2]}
                return {"ok": False, "detail": "Invalid credentials"}
    except Exception as e:
        return {"ok": False, "detail": str(e)}
        