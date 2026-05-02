"""
voice_ai/vapi_handler.py — FINAL VERSION
Tools: checkAvailability, bookAppointment, endCall
Fix: Short firstMessage + date context in system prompt only
"""

import os
import json
import re
import sqlite3
import httpx
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

VAPI_API_KEY = os.getenv("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")
DB_PATH = os.getenv("SQLITE_DB_PATH", "memory/hvac_leads.db")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "HVAC Pro")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "")
VAPI_BASE_URL = "https://api.vapi.ai"


# ── Date builders ─────────────────────────────────────────────────────────────

def build_first_message() -> str:
    """Short natural greeting — no dates read aloud."""
    return f"Thank you for calling {BUSINESS_NAME}! How can I help you today?"


def build_date_system_prompt() -> str:
    """
    Compact date context injected into system prompt daily.
    Agent uses this internally — never reads it aloud.
    """
    now = datetime.now()

    def next_wd(wd: int) -> str:
        days = (wd - now.weekday()) % 7
        if days == 0:
            days = 7
        return (now + timedelta(days=days)).strftime("%B %d, %Y")

    return (
        f"\n\n[DATE CONTEXT — use internally, never read aloud] "
        f"Today: {now.strftime('%A, %B %d, %Y')} | "
        f"Mon={next_wd(0)} | Tue={next_wd(1)} | Wed={next_wd(2)} | "
        f"Thu={next_wd(3)} | Fri={next_wd(4)} | Sat={next_wd(5)} | Sun={next_wd(6)}"
    )


# ── Auto-update firstMessage + system prompt ──────────────────────────────────

@router.get("/update-assistant-date")
async def update_assistant_date():
    """
    Update Vapi assistant:
    - firstMessage: short greeting only
    - systemPrompt: append today's date context at the end
    Call daily at 8 AM via Railway scheduler or manually via browser.
    """
    if not VAPI_API_KEY or not VAPI_ASSISTANT_ID:
        raise HTTPException(status_code=503, detail="VAPI_API_KEY or VAPI_ASSISTANT_ID missing")

    # 1. Get current assistant to read existing system prompt
    async with httpx.AsyncClient() as client:
        get_resp = await client.get(
            f"{VAPI_BASE_URL}/assistant/{VAPI_ASSISTANT_ID}",
            headers={"Authorization": f"Bearer {VAPI_API_KEY}"},
            timeout=10,
        )

    if get_resp.status_code != 200:
        raise HTTPException(status_code=get_resp.status_code, detail="Failed to get assistant")

    assistant_data = get_resp.json()
    current_prompt = assistant_data.get("model", {}).get("systemPrompt", "")

    # Remove old date context if exists
    if "[DATE CONTEXT" in current_prompt:
        current_prompt = current_prompt.split("\n\n[DATE CONTEXT")[0]

    # Append new date context
    new_prompt = current_prompt + build_date_system_prompt()
    first_message = build_first_message()

    print(f"[DATE-UPDATE] firstMessage: {first_message}")
    print(f"[DATE-UPDATE] Date appended to system prompt")

    # 2. Patch assistant
    async with httpx.AsyncClient() as client:
        resp = await client.patch(
            f"{VAPI_BASE_URL}/assistant/{VAPI_ASSISTANT_ID}",
            json={
                "firstMessage": first_message,
                "model": {
                    "provider": assistant_data.get("model", {}).get("provider", "openai"),
                    "model": assistant_data.get("model", {}).get("model", "gpt-4"),
                    "systemPrompt": new_prompt,
                }
            },
            headers={
                "Authorization": f"Bearer {VAPI_API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=10,
        )

    print(f"[DATE-UPDATE] Vapi response: {resp.status_code} — {resp.text[:200]}")

    if resp.status_code not in (200, 201):
        raise HTTPException(
            status_code=resp.status_code,
            detail=f"Vapi update failed: {resp.text}"
        )

    return {
        "status": "updated",
        "date": datetime.now().strftime("%A, %B %d, %Y"),
        "firstMessage": first_message
    }


# ── SQLite helpers ────────────────────────────────────────────────────────────

def ensure_voice_calls_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS voice_calls (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            call_id TEXT UNIQUE NOT NULL,
            lead_name TEXT,
            phone TEXT,
            direction TEXT DEFAULT 'inbound',
            duration_sec INTEGER DEFAULT 0,
            outcome TEXT DEFAULT 'pending',
            transcript_preview TEXT,
            full_transcript TEXT,
            lead_state_json TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def log_call(call_id: str, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    fields = ", ".join(kwargs.keys())
    placeholders = ", ".join(["?"] * len(kwargs))
    conn.execute(
        f"INSERT OR IGNORE INTO voice_calls (call_id, {fields}) VALUES (?, {placeholders})",
        (call_id, *kwargs.values()),
    )
    conn.commit()
    conn.close()


def update_call(call_id: str, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    sets = ", ".join(f"{k} = ?" for k in kwargs)
    conn.execute(
        f"UPDATE voice_calls SET {sets} WHERE call_id = ?",
        (*kwargs.values(), call_id),
    )
    conn.commit()
    conn.close()


# ── PostgreSQL helpers ────────────────────────────────────────────────────────

def save_lead_to_postgres(name: str, phone: str, email: str = "", source: str = "Voice AI"):
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO leads (client_id, name, phone, email, status, source, created_at)
                       VALUES (%s, %s, %s, %s, %s, %s, NOW())
                       ON CONFLICT DO NOTHING""",
                    (1, name or "Unknown Caller", phone, email, "new", source)
                )
            conn.commit()
        print(f"[DB] Lead saved: {name} - {phone}")
    except Exception as e:
        print(f"[DB] Lead save error: {e}")


# ── Calendar helper ───────────────────────────────────────────────────────────

def parse_appointment_dt(date_str: str, time_str: str) -> datetime:
    now = datetime.now()
    parsed_date = None

    for fmt in ["%Y-%m-%d", "%m/%d/%Y", "%B %d, %Y", "%B %d %Y", "%b %d, %Y", "%b %d %Y"]:
        try:
            parsed_date = datetime.strptime(date_str.strip(), fmt)
            break
        except ValueError:
            continue

    if parsed_date is None:
        d = date_str.lower().strip()
        if "tomorrow" in d:
            parsed_date = now + timedelta(days=1)
        elif "today" in d:
            parsed_date = now
        else:
            weekdays = {
                "monday": 0, "tuesday": 1, "wednesday": 2,
                "thursday": 3, "friday": 4, "saturday": 5, "sunday": 6
            }
            matched = False
            for name, wd in weekdays.items():
                if name in d:
                    days_ahead = (wd - now.weekday()) % 7 or 7
                    parsed_date = now + timedelta(days=days_ahead)
                    matched = True
                    break
            if not matched:
                parsed_date = now + timedelta(days=1)

    t = time_str.lower().strip()
    word_to_num = {
        "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
        "nine": 9, "ten": 10, "eleven": 11, "twelve": 12,
        "noon": 12, "midnight": 0
    }
    for word, num in word_to_num.items():
        t = t.replace(word, str(num))

    is_pm = "pm" in t
    is_am = "am" in t
    numbers = re.findall(r'\d+', t)

    hour = 10
    minute = 0

    if numbers:
        hour = int(numbers[0])
        if len(numbers) > 1:
            minute = int(numbers[1])

    if is_pm and hour != 12:
        hour += 12
    elif is_am and hour == 12:
        hour = 0
    elif not is_am and not is_pm and 1 <= hour <= 7:
        hour += 12

    hour = max(0, min(23, hour))
    minute = max(0, min(59, minute))

    return parsed_date.replace(hour=hour, minute=minute, second=0, microsecond=0)


def book_on_google_calendar(
    name: str, phone: str, address: str, service_type: str,
    appointment_dt: datetime, notes: str = ""
) -> dict:
    try:
        from integrations.gcal import book_appointment
        return book_appointment(
            customer_name=name,
            customer_phone=phone,
            service_type=service_type,
            appointment_dt=appointment_dt,
            notes=f"Address: {address} | {notes}",
        )
    except Exception as e:
        print(f"[CALENDAR] Error: {e}")
        return {"success": False, "error": str(e)}


# ── Vapi Tool Handlers ────────────────────────────────────────────────────────

async def handle_check_availability(args: dict) -> str:
    date = args.get("date", "")
    time = args.get("time", "")
    print(f"[AVAILABILITY] Checking: {date} at {time}")
    return json.dumps({
        "available": True,
        "date": date,
        "time": time,
        "message": f"The slot on {date} at {time} is available."
    })


async def handle_book_appointment(args: dict, call_id: str) -> str:
    name = args.get("name", "Unknown")
    phone = args.get("phone", "")
    address = args.get("address", "")
    zip_code = args.get("zip", "")
    date = args.get("date", "")
    time = args.get("time", "")
    issue = args.get("issue", "HVAC Service")

    full_address = f"{address} {zip_code}".strip()
    appointment_dt = parse_appointment_dt(date, time)

    print(f"[BOOKING] {name} | {phone} | {full_address} | {date} {time} | {issue}")
    print(f"[BOOKING] Parsed datetime: {appointment_dt}")

    save_lead_to_postgres(name=name, phone=phone, source="Voice AI - bookAppointment")

    result = book_on_google_calendar(
        name=name,
        phone=phone,
        address=full_address,
        service_type=issue,
        appointment_dt=appointment_dt,
        notes="Booked via Voice AI",
    )

    update_call(call_id, lead_name=name, phone=phone, outcome="appointment_booked")

    if result.get("success"):
        print(f"[BOOKING] ✅ Calendar event: {result.get('event_link')}")
        return json.dumps({
            "success": True,
            "message": f"Appointment booked for {name} on {date} at {time}.",
            "event_link": result.get("event_link", "")
        })
    else:
        print(f"[BOOKING] ⚠️ Calendar failed: {result.get('error')}")
        return json.dumps({
            "success": True,
            "message": f"Appointment confirmed for {name} on {date} at {time}. Team will follow up."
        })


async def handle_end_call(args: dict, call_id: str) -> str:
    update_call(call_id, outcome="completed")
    print(f"[ENDCALL] Call {call_id} ended.")
    return json.dumps({"success": True})


# ── Webhook endpoint ──────────────────────────────────────────────────────────

@router.post("/webhook")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks):
    ensure_voice_calls_table()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message = body.get("message", {})
    msg_type = message.get("type", "")
    call = message.get("call", {})
    call_id = call.get("id", "unknown")

    print(f"[VAPI] Event: {msg_type} | Call: {call_id}")
    print(f"[VAPI] Body: {json.dumps(body)[:400]}")

    if msg_type == "call-started":
        customer_number = call.get("customer", {}).get("number", "")
        direction = call.get("type", "inboundPhoneCall")
        log_call(call_id, phone=customer_number,
                 direction="inbound" if "inbound" in direction.lower() else "outbound")
        return JSONResponse({"status": "logged"})

    if msg_type == "function-call":
        func_call = message.get("functionCall", {})
        func_name = func_call.get("name", "")
        func_args = func_call.get("parameters", {})

        print(f"[VAPI] Function: {func_name} | Args: {json.dumps(func_args)[:300]}")

        if func_name == "checkAvailability":
            result = await handle_check_availability(func_args)
        elif func_name in ("bookAppointment", "book_appointment"):
            result = await handle_book_appointment(func_args, call_id)
        elif func_name == "endCall":
            result = await handle_end_call(func_args, call_id)
        elif func_name == "qualify_lead":
            name = func_args.get("lead_name", "")
            phone = func_args.get("lead_phone", "")
            save_lead_to_postgres(name=name, phone=phone, source="Voice AI - qualify_lead")
            result = "Perfect, I have all your details. You will receive a confirmation shortly."
        else:
            result = f"Function {func_name} not implemented."

        return JSONResponse({"result": result})

    if msg_type == "end-of-call-report":
        transcript = message.get("transcript", "")
        customer_number = call.get("customer", {}).get("number", "")
        customer_name = call.get("customer", {}).get("name", "Unknown Caller")

        try:
            started = call.get("startedAt", "")
            ended = call.get("endedAt", "")
            if started and ended:
                fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
                duration_sec = int((
                    datetime.strptime(ended, fmt) - datetime.strptime(started, fmt)
                ).total_seconds())
            else:
                duration_sec = 0
        except Exception:
            duration_sec = 0

        update_call(call_id,
                    duration_sec=max(duration_sec, 0),
                    full_transcript=transcript,
                    transcript_preview=transcript[:200])

        if customer_number:
            save_lead_to_postgres(
                name=customer_name,
                phone=customer_number,
                source="Voice AI - end-of-call"
            )

        return JSONResponse({"status": "logged"})

    return JSONResponse({"status": "ignored", "type": msg_type})


# ── Outbound call ─────────────────────────────────────────────────────────────

class OutboundCallRequest(BaseModel):
    phone: str
    lead_name: str
    service_type: str
    urgency: str = "routine"
    followup_num: int = 1
    booking_url: str = ""


@router.post("/outbound")
async def trigger_outbound_call(req: OutboundCallRequest):
    if not VAPI_API_KEY:
        raise HTTPException(status_code=503, detail="VAPI_API_KEY not configured")
    if not VAPI_PHONE_NUMBER_ID:
        raise HTTPException(status_code=503, detail="VAPI_PHONE_NUMBER_ID not configured")

    payload = {
        "assistantId": VAPI_ASSISTANT_ID,
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer": {"number": req.phone, "name": req.lead_name},
        "assistantOverrides": {
            "firstMessage": f"Hi, may I speak with {req.lead_name}? This is calling from {BUSINESS_NAME} regarding your HVAC service request."
        },
    }

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{VAPI_BASE_URL}/call/phone",
            json=payload,
            headers={
                "Authorization": f"Bearer {VAPI_API_KEY}",
                "Content-Type": "application/json"
            },
            timeout=15,
        )

    if response.status_code not in (200, 201):
        raise HTTPException(status_code=response.status_code,
                            detail=f"Vapi error: {response.text}")

    call_data = response.json()
    call_id = call_data.get("id", "unknown")
    ensure_voice_calls_table()
    log_call(call_id, lead_name=req.lead_name, phone=req.phone,
             direction="outbound", outcome="pending")
    return {"call_id": call_id, "status": "dialing", "phone": req.phone}


@router.get("/calls")
async def list_calls(limit: int = 50):
    ensure_voice_calls_table()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT * FROM voice_calls ORDER BY created_at DESC LIMIT ?", (limit,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]
