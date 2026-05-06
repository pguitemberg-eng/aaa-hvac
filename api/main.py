"""
api/main.py
Main FastAPI app. All routers mounted here.
Run: uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

import sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import logging
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("aaa-hvac")

scheduler = AsyncIOScheduler()


def scheduler_listener(event):
    if event.exception:
        logger.error(f"[SCHEDULER] Job {event.job_id} FAILED: {event.exception}")
    else:
        logger.info(f"[SCHEDULER] Job {event.job_id} completed successfully")


def get_allowed_origins() -> list[str]:
    origins = [
        "http://localhost:8000",
        "http://localhost:8501",
        "https://dashboard.midvio.com",
    ]
    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "")
    if railway_domain:
        origins.append(f"https://{railway_domain}")
    extra = os.getenv("CORS_ALLOWED_ORIGINS", "")
    if extra:
        origins.extend([o.strip() for o in extra.split(",") if o.strip()])
    return origins


# ── Daily date update job ─────────────────────────────────────────────────────

async def daily_update_assistant_date():
    """Update Vapi assistant firstMessage with today's date every morning at 8 AM."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                "https://api.midvio.com/vapi/update-assistant-date",
                timeout=15,
            )
        logger.info(f"[SCHEDULER] daily_update_assistant_date → {resp.status_code}")
    except Exception as e:
        logger.error(f"[SCHEDULER] daily_update_assistant_date FAILED: {e}")


@asynccontextmanager
async def lifespan(app: FastAPI):
    anthropic_key_loaded = False
    try:
        _ = get_anthropic_api_key()
        anthropic_key_loaded = True
    except Exception:
        anthropic_key_loaded = False

    logger.info(f"[MAIN] Anthropic key loaded: {str(anthropic_key_loaded).lower()}")
    scheduler.add_listener(scheduler_listener, EVENT_JOB_ERROR | EVENT_JOB_EXECUTED)

    # ── Appointment reminders every 30 min ────────────────────────────────────
    scheduler.add_job(
        send_appointment_reminders,
        "interval",
        minutes=30,
        id="appointment_reminders",
        replace_existing=True,
        misfire_grace_time=60,
    )

    # ── Update Vapi assistant date every day at 8 AM ──────────────────────────
    scheduler.add_job(
        daily_update_assistant_date,
        "cron",
        hour=8,
        minute=0,
        id="daily_update_assistant_date",
        replace_existing=True,
        misfire_grace_time=300,
    )

    scheduler.start()
    logger.info("[MAIN] Scheduler started — appointment reminders every 30 min")
    logger.info("[MAIN] Scheduler started — assistant date update every day at 8 AM")
    logger.info(f"[MAIN] CORS allowed origins: {get_allowed_origins()}")

    # ── Run date update immediately on startup ────────────────────────────────
    try:
        await daily_update_assistant_date()
        logger.info("[MAIN] Assistant date updated on startup")
    except Exception as e:
        logger.warning(f"[MAIN] Startup date update failed (non-fatal): {e}")

    yield

    scheduler.shutdown(wait=False)
    logger.info("[MAIN] Scheduler stopped")


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

from missed_call.missed_call_handler import router as missed_call_router
from booking.booking_handler import router as booking_router
from speed_to_lead.speed_to_lead import router as speed_router
from voice_ai.vapi_handler import router as vapi_router
from api.onboarding import router as onboarding_router

app.include_router(missed_call_router, prefix="/twilio",     tags=["Missed Call"])
app.include_router(booking_router,     prefix="/booking",    tags=["Booking"])
app.include_router(speed_router,       prefix="/lead",       tags=["Speed To Lead"])
app.include_router(vapi_router,        prefix="/vapi",       tags=["Voice AI"])
app.include_router(onboarding_router,  prefix="/onboarding", tags=["Onboarding"])


class DashboardLoginRequest(BaseModel):
    username: str
    password: str


class ResetClientRequest(BaseModel):
    username: str
    password: str
    business_name: str


@app.post("/auth/dashboard")
async def dashboard_login(req: DashboardLoginRequest):
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


@app.get("/health", tags=["System"])
async def health():
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
async def get_leads(client_id: int = None):
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                if client_id:
                    cursor.execute(
                        "SELECT id, phone, status, created_at, name FROM leads WHERE client_id = %s ORDER BY created_at DESC",
                        (client_id,)
                    )
                else:
                    cursor.execute(
                        "SELECT id, phone, status, created_at, name FROM leads ORDER BY created_at DESC"
                    )
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
                    cursor.execute(
                        "SELECT id, lead_name, phone, service_type, scheduled_at, status FROM appointments WHERE client_id = %s ORDER BY created_at DESC",
                        (client_id,)
                    )
                else:
                    cursor.execute(
                        "SELECT id, lead_name, phone, service_type, scheduled_at, status FROM appointments ORDER BY created_at DESC"
                    )
                rows = cursor.fetchall()
                def _fmt_sched(dt):
                    if not dt:
                        return ""
                    return dt.strftime("%a, %b ") + str(dt.day) + dt.strftime(" • %I:%M %p").replace(" 0", " ")

                return {"appointments": [{"id":r[0],"name":r[1],"phone":r[2],"type":r[3],"time":_fmt_sched(r[4]),"status":r[5]} for r in rows]}
    except Exception as e:
        return {"appointments": [], "error": str(e)}


@app.get("/voice-calls")
async def get_voice_calls(client_id: int = None):
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cursor:
                if client_id:
                    cursor.execute(
                        "SELECT id, caller_name, phone, call_type, duration, status, created_at FROM voice_calls WHERE client_id = %s ORDER BY created_at DESC",
                        (client_id,)
                    )
                else:
                    cursor.execute(
                        "SELECT id, caller_name, phone, call_type, duration, status, created_at FROM voice_calls ORDER BY created_at DESC"
                    )
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
                cursor.execute(
                    "SELECT id, company_name, username, phone_number, active FROM clients ORDER BY company_name"
                )
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


@app.post("/admin/reset-client")
async def admin_reset_client(req: ResetClientRequest):
    import bcrypt
    try:
        from db.postgres import get_conn
        password_hash = bcrypt.hashpw(req.password.encode(), bcrypt.gensalt()).decode()
        with get_conn() as conn:
            with conn.cursor() as cursor:
                cursor.execute(
                    """
                    INSERT INTO clients (username, password_hash, company_name, active)
                    VALUES (%s, %s, %s, true)
                    ON CONFLICT (username) DO UPDATE
                    SET password_hash = EXCLUDED.password_hash,
                        company_name = EXCLUDED.company_name,
                        active = true
                    """,
                    (req.username, password_hash, req.business_name),
                )
            conn.commit()
        return {"ok": True, "username": req.username}
    except Exception as e:
        return {"ok": False, "detail": str(e)}