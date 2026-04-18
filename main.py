"""
api/main.py
Main FastAPI app. All routers mounted here.
Run: uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
"""

from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv

load_dotenv()

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from booking.booking_handler import send_appointment_reminders

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    scheduler.add_job(send_appointment_reminders, "interval", minutes=30)
    scheduler.start()
    print("[MAIN] Scheduler started")
    yield
    scheduler.shutdown()


app = FastAPI(
    title="AAA HVAC - AI Automation API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

from missed_call.missed_call_handler import router as missed_call_router
from booking.booking_handler import router as booking_router
from speed_to_lead.speed_to_lead import router as speed_router
from voice_ai.vapi_handler import router as vapi_router
from api.onboarding import router as onboarding_router

app.include_router(missed_call_router, prefix="/twilio", tags=["Missed Call"])
app.include_router(booking_router, prefix="/booking", tags=["Booking"])
app.include_router(speed_router, prefix="/lead", tags=["Speed To Lead"])
app.include_router(vapi_router, prefix="/vapi", tags=["Voice AI"])
app.include_router(onboarding_router, prefix="/onboarding", tags=["Onboarding"])


@app.get("/health")
async def health():
    return {"status": "ok", "service": "AAA HVAC AI"}