"""
voice_ai/vapi_handler.py
"""

import os
import json
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


def save_lead_to_postgres(name: str, phone: str, email: str = "", source: str = "Voice AI"):
    """Save lead to Neon PostgreSQL database."""
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
        print(f"[PostgreSQL] Lead saved: {name} - {phone}")
    except Exception as e:
        print(f"[PostgreSQL] Lead save error: {e}")


def book_google_calendar(
    customer_name: str,
    customer_phone: str,
    service_type: str,
    appointment_dt: datetime,
    notes: str = "",
) -> dict:
    """Book appointment on Google Calendar."""
    try:
        from integrations.gcal import book_appointment
        result = book_appointment(
            customer_name=customer_name,
            customer_phone=customer_phone,
            service_type=service_type,
            appointment_dt=appointment_dt,
            notes=notes,
        )
        print(f"[CALENDAR] Booking result: {result}")
        return result
    except Exception as e:
        print(f"[CALENDAR] Booking error: {e}")
        return {"success": False, "error": str(e)}


def parse_appointment_time(date_str: str, time_str: str) -> Optional[datetime]:
    """Parse date and time strings into datetime object."""
    try:
        now = datetime.now()
        # Try common formats
        for fmt in ["%Y-%m-%d %H:%M", "%m/%d/%Y %H:%M", "%B %d %Y %H:%M"]:
            try:
                return datetime.strptime(f"{date_str} {time_str}", fmt)
            except ValueError:
                continue

        # Fallback: tomorrow at 10 AM
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)
    except Exception:
        tomorrow = datetime.now() + timedelta(days=1)
        return tomorrow.replace(hour=10, minute=0, second=0, microsecond=0)


async def handle_qualify_lead(args: dict, call_id: str) -> str:
    from langchain_core.messages import HumanMessage
    lead_state = {
        "messages": [HumanMessage(content=args.get("lead_service_type", ""))],
        "lead_name": args.get("lead_name", ""),
        "lead_phone": args.get("lead_phone", ""),
        "lead_email": args.get("lead_email", ""),
        "lead_address": args.get("lead_address", ""),
        "lead_service_type": args.get("lead_service_type", ""),
        "lead_urgency": args.get("lead_urgency", "routine"),
        "lead_budget": args.get("lead_budget", ""),
        "appointment_date": args.get("appointment_date", ""),
        "appointment_time": args.get("appointment_time", ""),
        "followup_count": 0,
        "followup_max": 3,
        "booking_confirmed": False,
        "source": "vapi_voice_call",
        "outcome": "",
        "error": "",
    }

    update_call(call_id,
                lead_name=lead_state["lead_name"],
                phone=lead_state["lead_phone"],
                lead_state_json=json.dumps(lead_state))

    # Save lead to PostgreSQL
    save_lead_to_postgres(
        name=lead_state["lead_name"],
        phone=lead_state["lead_phone"],
        email=lead_state["lead_email"],
        source="Voice AI - qualify_lead"
    )

    # ✅ Book Google Calendar appointment if date/time provided
    appt_date = args.get("appointment_date", "")
    appt_time = args.get("appointment_time", "")
    if appt_date or appt_time:
        appointment_dt = parse_appointment_time(appt_date, appt_time)
        book_google_calendar(
            customer_name=lead_state["lead_name"],
            customer_phone=lead_state["lead_phone"],
            service_type=lead_state["lead_service_type"],
            appointment_dt=appointment_dt,
            notes=f"Urgency: {lead_state['lead_urgency']}",
        )

    asyncio.create_task(_run_agent_async(lead_state, call_id))
    urgency = args.get("lead_urgency", "routine")
    if urgency == "emergency":
        return "I have submitted your request as EMERGENCY priority. You will receive a text with a booking link in 30 seconds."
    return "Perfect, I have all your details. You will receive a text message shortly with a link to book your appointment."


async def _run_agent_async(lead_state: dict, call_id: str):
    try:
        import sys
        sys.path.insert(0, ".")
        from agent.graph import build_graph
        graph = build_graph()
        result = graph.invoke(lead_state)
        update_call(call_id, outcome=result.get("outcome", "unknown"))
    except Exception as e:
        print(f"[VAPI] Agent error: {e}")
        update_call(call_id, outcome="agent_error")


async def handle_lookup_lead(args: dict) -> str:
    phone = args.get("phone", "")
    try:
        from db.postgres import get_conn
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT name FROM leads WHERE phone = %s LIMIT 1", (phone,))
                row = cur.fetchone()
        if row:
            return f"Welcome back, {row[0]}. How can we help you today?"
    except Exception:
        pass
    return "This appears to be a new customer account."


async def handle_escalate_call(args: dict, call_id: str) -> str:
    phone = args.get("lead_phone", "")
    name = args.get("lead_name", "Unknown")
    reason = args.get("reason", "")
    try:
        from tools.twilio_tool import send_sms
        if BUSINESS_PHONE:
            send_sms(to=BUSINESS_PHONE,
                     body=f"CALL ESCALATION: {name} ({phone}) needs manual callback. Reason: {reason}")
    except Exception as e:
        print(f"[VAPI] Escalation SMS failed: {e}")
    update_call(call_id, outcome="escalated")
    return "I am flagging your request right now and one of our team members will call you back shortly."


async def handle_book_appointment(args: dict, call_id: str) -> str:
    """Handle direct book_appointment function call from Vapi."""
    customer_name = args.get("customer_name", args.get("lead_name", "Unknown"))
    customer_phone = args.get("customer_phone", args.get("lead_phone", ""))
    service_type = args.get("service_type", args.get("lead_service_type", "HVAC Service"))
    appt_date = args.get("appointment_date", "")
    appt_time = args.get("appointment_time", "")
    notes = args.get("notes", "")

    appointment_dt = parse_appointment_time(appt_date, appt_time)

    result = book_google_calendar(
        customer_name=customer_name,
        customer_phone=customer_phone,
        service_type=service_type,
        appointment_dt=appointment_dt,
        notes=notes,
    )

    if result.get("success"):
        update_call(call_id, outcome="appointment_booked")
        return f"Perfect! Your appointment for {service_type} has been scheduled. You will receive a confirmation shortly."
    else:
        return "I have noted your preferred time. Our team will confirm your appointment shortly."


@router.post("/webhook")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks):
    ensure_voice_calls_table()
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Log all events for debugging
    print(f"[VAPI WEBHOOK] Event type: {body.get('message', {}).get('type', 'unknown')}")
    print(f"[VAPI WEBHOOK] Body preview: {json.dumps(body)[:300]}")

    message = body.get("message", {})
    msg_type = message.get("type", "")
    call = message.get("call", {})
    call_id = call.get("id", "unknown")

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

        print(f"[VAPI] Function called: {func_name} with args: {func_args}")

        if func_name == "qualify_lead":
            result = await handle_qualify_lead(func_args, call_id)
        elif func_name == "book_appointment":
            result = await handle_book_appointment(func_args, call_id)
        elif func_name == "lookup_lead":
            result = await handle_lookup_lead(func_args)
        elif func_name == "escalate_call":
            result = await handle_escalate_call(func_args, call_id)
        else:
            result = f"Function {func_name} not implemented."
        return JSONResponse({"result": result})

    if msg_type == "end-of-call-report":
        transcript = message.get("transcript", "")
        summary = message.get("summary", "")

        customer_number = call.get("customer", {}).get("number", "")
        customer_name = call.get("customer", {}).get("name", "Unknown Caller")

        try:
            started = call.get("startedAt", "")
            ended = call.get("endedAt", "")
            if started and ended:
                fmt = "%Y-%m-%dT%H:%M:%S.%fZ"
                duration_sec = int((datetime.strptime(ended, fmt) - datetime.strptime(started, fmt)).total_seconds())
            else:
                duration_sec = 0
        except Exception:
            duration_sec = 0

        update_call(call_id,
                    duration_sec=max(duration_sec, 0),
                    full_transcript=transcript,
                    transcript_preview=transcript[:200],
                    outcome="completed")

        # ✅ Save lead automatically on end-of-call
        if customer_number:
            save_lead_to_postgres(
                name=customer_name,
                phone=customer_number,
                source="Voice AI - end-of-call"
            )
            print(f"[VAPI] Lead saved from end-of-call: {customer_name} - {customer_number}")

        return JSONResponse({"status": "logged"})

    return JSONResponse({"status": "ignored", "type": msg_type})


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
            headers={"Authorization": f"Bearer {VAPI_API_KEY}",
                     "Content-Type": "application/json"},
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