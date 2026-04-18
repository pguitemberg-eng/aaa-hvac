"""
missed_call/missed_call_handler.py
Windows-compatible version.

Twilio setup:
  Phone Numbers -> your number -> Voice webhook -> POST -> https://yourdomain.com/twilio/inbound
"""

import os
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import Response, JSONResponse
from twilio.twiml.voice_response import VoiceResponse
from twilio.rest import Client as TwilioClient
from dotenv import load_dotenv

from db.postgres import get_conn

load_dotenv()

router = APIRouter()

TWILIO_SID = os.getenv("TWILIO_ACCOUNT_SID", "")
TWILIO_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "")
TWILIO_FROM = os.getenv("TWILIO_PHONE_NUMBER", "")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "HVAC Pro")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "")

ACK_SMS = (
    "Hi! You just called {business}. Sorry we missed you. "
    "We can help with your HVAC issue right now. "
    "Reply with your name and what is going on and we will get back to you instantly. "
    "Or book here: {booking_url}"
)


def ensure_missed_calls_table():
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS missed_calls (
            id BIGSERIAL PRIMARY KEY,
            phone TEXT NOT NULL,
            call_sid TEXT,
            sms_sent INTEGER DEFAULT 0,
            replied INTEGER DEFAULT 0,
            reply_text TEXT,
            outcome TEXT DEFAULT 'pending',
            created_at TIMESTAMPTZ DEFAULT NOW(),
            replied_at TEXT
        )
    """)
        conn.commit()


def log_missed_call(phone: str, call_sid: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO missed_calls (phone, call_sid) VALUES (%s, %s) RETURNING id",
            (phone, call_sid),
        )
        row = cur.fetchone()
        conn.commit()
        return row[0] if row else 0


def update_missed_call(phone: str, **kwargs):
    with get_conn() as conn:
        sets = ", ".join(f"{k} = %s" for k in kwargs)
        conn.execute(
            f"""UPDATE missed_calls
                SET {sets}
                WHERE id = (
                    SELECT id FROM missed_calls
                    WHERE phone = %s
                    ORDER BY created_at DESC
                    LIMIT 1
                )""",
            (*kwargs.values(), phone),
        )
        conn.commit()


def send_immediate_sms(to_phone: str) -> bool:
    if not all([TWILIO_SID, TWILIO_TOKEN, TWILIO_FROM]):
        print(f"[MISSED_CALL] Twilio not configured - skipping SMS to {to_phone}")
        return False

    booking_url = os.getenv("CALENDLY_GENERIC_URL", "")
    body = ACK_SMS.format(
        business=BUSINESS_NAME,
        booking_url=booking_url if booking_url else "reply and we will send a link",
    )

    try:
        client = TwilioClient(TWILIO_SID, TWILIO_TOKEN)
        msg = client.messages.create(to=to_phone, from_=TWILIO_FROM, body=body)
        print(f"[MISSED_CALL] SMS sent to {to_phone} | sid={msg.sid}")
        return True
    except Exception as e:
        print(f"[MISSED_CALL] SMS failed: {e}")
        return False


@router.post("/inbound")
async def handle_inbound_call(
    request: Request,
    From: str = Form(default=""),
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
):
    caller = From
    response = VoiceResponse()
    vapi_phone = os.getenv("VAPI_FORWARDING_NUMBER", "")

    if vapi_phone:
        dial = response.dial(timeout=20, action="/twilio/missed-call-fallback")
        dial.number(vapi_phone)
    else:
        response.say(
            f"You have reached {BUSINESS_NAME}. "
            "We are sending you a text message right now so you can book your appointment immediately.",
            voice="Polly.Joanna",
        )
        response.hangup()
        asyncio.create_task(_fire_missed_call_flow(caller, CallSid))

    return Response(content=str(response), media_type="application/xml")


@router.post("/missed-call-fallback")
async def handle_missed_call_fallback(
    request: Request,
    From: str = Form(default=""),
    CallSid: str = Form(default=""),
    DialCallStatus: str = Form(default=""),
):
    caller = From
    print(f"[MISSED_CALL] Dial ended | status={DialCallStatus} | caller={caller}")

    if DialCallStatus in ("no-answer", "busy", "failed", "canceled"):
        asyncio.create_task(_fire_missed_call_flow(caller, CallSid))

    response = VoiceResponse()
    response.hangup()
    return Response(content=str(response), media_type="application/xml")


@router.post("/missed-call")
async def handle_missed_call_direct(
    request: Request,
    From: str = Form(default=""),
    CallSid: str = Form(default=""),
    CallStatus: str = Form(default=""),
):
    caller = From
    print(f"[MISSED_CALL] Direct | caller={caller} | status={CallStatus}")

    ensure_missed_calls_table()
    log_missed_call(caller, CallSid)
    asyncio.create_task(_fire_missed_call_flow(caller, CallSid))

    response = VoiceResponse()
    response.hangup()
    return Response(content=str(response), media_type="application/xml")


async def _fire_missed_call_flow(caller: str, call_sid: str):
    print(f"[MISSED_CALL] Firing flow for {caller}")
    ensure_missed_calls_table()
    log_missed_call(caller, call_sid)

    sms_ok = send_immediate_sms(caller)
    if sms_ok:
        update_missed_call(caller, sms_sent=1)

    await _run_agent_for_missed_call(caller)


async def _run_agent_for_missed_call(phone: str):
    try:
        import sys
        sys.path.insert(0, ".")
        from langchain_core.messages import HumanMessage
        from agent.graph import build_graph

        lead_state = {
            "messages": [HumanMessage(content="Customer called and hung up - missed call. Send a booking link immediately.")],
            "lead_name": "Missed Call",
            "lead_phone": phone,
            "lead_email": "",
            "lead_address": "",
            "lead_service_type": "HVAC service - details pending",
            "lead_urgency": "urgent",
            "lead_budget": "",
            "followup_count": 0,
            "followup_max": 3,
            "booking_confirmed": False,
            "source": "missed_call",
            "outcome": "",
            "error": "",
        }

        graph = build_graph()
        result = graph.invoke(lead_state)
        outcome = result.get("outcome", "unknown")
        update_missed_call(phone, outcome=outcome)
        print(f"[MISSED_CALL] Agent done | {phone} | outcome={outcome}")

    except Exception as e:
        print(f"[MISSED_CALL] Agent error: {e}")


@router.post("/sms-reply")
async def handle_sms_reply(
    request: Request,
    From: str = Form(default=""),
    Body: str = Form(default=""),
    MessageSid: str = Form(default=""),
):
    caller = From
    message = Body.strip()
    print(f"[SMS_REPLY] From={caller} | Body={message[:80]}")

    ensure_missed_calls_table()
    update_missed_call(caller, replied=1, reply_text=message, replied_at=datetime.now().isoformat())
    asyncio.create_task(_run_agent_with_reply(caller, message))
    return Response(content="<Response/>", media_type="application/xml")


async def _run_agent_with_reply(phone: str, reply_text: str):
    try:
        import sys
        sys.path.insert(0, ".")
        from langchain_core.messages import HumanMessage
        from agent.graph import build_graph

        name = "Customer"
        parts = reply_text.split(",", 1)
        if len(parts) > 1 and len(parts[0].split()) <= 3:
            name = parts[0].strip()
            service = parts[1].strip()
        else:
            service = reply_text

        urgency = _detect_urgency(reply_text)

        lead_state = {
            "messages": [HumanMessage(content=reply_text)],
            "lead_name": name,
            "lead_phone": phone,
            "lead_email": "",
            "lead_address": "",
            "lead_service_type": service,
            "lead_urgency": urgency,
            "lead_budget": "",
            "followup_count": 0,
            "followup_max": 3,
            "booking_confirmed": False,
            "source": "missed_call_reply",
            "outcome": "",
            "error": "",
        }

        graph = build_graph()
        result = graph.invoke(lead_state)
        print(f"[SMS_REPLY] Agent done | {phone} | outcome={result.get('outcome')}")

    except Exception as e:
        print(f"[SMS_REPLY] Agent error: {e}")


def _detect_urgency(text: str) -> str:
    text_lower = text.lower()
    emergency_words = [
        "emergency", "no heat", "no ac", "no cooling", "flooding",
        "gas smell", "smoke", "urgent", "asap", "right now", "today",
        "freezing", "burning", "overheating",
    ]
    if any(w in text_lower for w in emergency_words):
        return "emergency"
    return "urgent"


@router.get("/status")
async def missed_call_status():
    ensure_missed_calls_table()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT phone, sms_sent, replied, outcome, created_at FROM missed_calls ORDER BY created_at DESC LIMIT 20"
        ).fetchall()
    return {
        "status": "ok",
        "recent_missed_calls": [
            {"phone": r[0], "sms_sent": r[1], "replied": r[2], "outcome": r[3], "at": r[4]}
            for r in rows
        ],
    }