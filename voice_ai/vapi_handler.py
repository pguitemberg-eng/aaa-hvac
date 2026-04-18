"""
voice_ai/vapi_handler.py

Fixes applied:
  - ensure_voice_calls_table() removed from hot path (runs once in lifespan)
  - Duration parsed correctly from ISO timestamp strings
  - Twilio force-hangup removed (call already ended at end-of-call-report)
  - asyncio.create_task() replaced with FastAPI BackgroundTasks
  - log_call() column whitelist prevents SQL injection
"""

import json
import os
from datetime import datetime, timezone
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel

from db.postgres import get_conn

load_dotenv()

router = APIRouter()

VAPI_API_KEY         = os.getenv("VAPI_API_KEY", "")
VAPI_ASSISTANT_ID    = os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_PHONE_NUMBER_ID = os.getenv("VAPI_PHONE_NUMBER_ID", "")
BUSINESS_NAME        = os.getenv("BUSINESS_NAME", "HVAC Pro")
BUSINESS_PHONE       = os.getenv("BUSINESS_PHONE", "")
VAPI_BASE_URL        = "https://api.vapi.ai"

# ── Allowed columns for log_call (prevents SQL injection) ─────────────────────
_ALLOWED_CALL_COLUMNS = {
    "lead_name", "phone", "direction", "duration_sec", "outcome",
    "transcript_preview", "full_transcript", "lead_state_json", "client_id",
}


# ── DB helpers ────────────────────────────────────────────────────────────────

def log_call(call_id: str, **kwargs):
    """Insert or update a voice_calls row. Only whitelisted columns accepted."""
    safe = {k: v for k, v in kwargs.items() if k in _ALLOWED_CALL_COLUMNS}
    if not safe:
        return
    fields       = ", ".join(safe.keys())
    placeholders = ", ".join(["%s"] * len(safe))
    updates      = ", ".join(f"{k} = EXCLUDED.{k}" for k in safe.keys())
    with get_conn() as conn:
        conn.execute(
            f"""
            INSERT INTO voice_calls (call_id, {fields})
            VALUES (%s, {placeholders})
            ON CONFLICT (call_id) DO UPDATE SET {updates}
            """,
            (call_id, *safe.values()),
        )
        conn.commit()


def update_call(call_id: str, **kwargs):
    """Update specific columns on an existing voice_calls row."""
    safe = {k: v for k, v in kwargs.items() if k in _ALLOWED_CALL_COLUMNS}
    if not safe:
        return
    sets = ", ".join(f"{k} = %s" for k in safe.keys())
    with get_conn() as conn:
        conn.execute(
            f"UPDATE voice_calls SET {sets} WHERE call_id = %s",
            (*safe.values(), call_id),
        )
        conn.commit()


def get_customer_by_phone(phone: str) -> Optional[dict]:
    with get_conn(row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM customers WHERE phone = %s LIMIT 1", (phone,)
        ).fetchone()
        return dict(row) if row else None


def _parse_duration(started_at, ended_at) -> int:
    """Parse ISO timestamp strings and return duration in seconds."""
    try:
        fmt   = "%Y-%m-%dT%H:%M:%S.%fZ"
        start = datetime.strptime(str(started_at), fmt).replace(tzinfo=timezone.utc)
        end   = datetime.strptime(str(ended_at),   fmt).replace(tzinfo=timezone.utc)
        return max(int((end - start).total_seconds()), 0)
    except Exception:
        return 0


# ── Agent helpers ─────────────────────────────────────────────────────────────

async def handle_qualify_lead(args: dict, call_id: str, background_tasks: BackgroundTasks) -> str:
    from langchain_core.messages import HumanMessage

    lead_state = {
        "messages":          [HumanMessage(content=args.get("lead_service_type", ""))],
        "lead_name":         args.get("lead_name", ""),
        "lead_phone":        args.get("lead_phone", ""),
        "lead_email":        args.get("lead_email", ""),
        "lead_address":      args.get("lead_address", ""),
        "lead_service_type": args.get("lead_service_type", ""),
        "lead_urgency":      args.get("lead_urgency", "routine"),
        "lead_budget":       args.get("lead_budget", ""),
        "followup_count":    0,
        "followup_max":      3,
        "booking_confirmed": False,
        "source":            "vapi_voice_call",
        "outcome":           "",
        "error":             "",
    }

    update_call(
        call_id,
        lead_name=lead_state["lead_name"],
        phone=lead_state["lead_phone"],
        lead_state_json=json.dumps(lead_state),
    )

    # ✅ BackgroundTasks — safe in FastAPI
    background_tasks.add_task(_run_agent_background, lead_state, call_id)

    urgency = args.get("lead_urgency", "routine")
    if urgency == "emergency":
        return "I have submitted your request as EMERGENCY priority. You will receive a text with a booking link in 30 seconds."
    return "Perfect, I have all your details. You will receive a text message shortly with a link to book your appointment."


def _run_agent_background(lead_state: dict, call_id: str):
    """Synchronous wrapper — runs in FastAPI thread pool via BackgroundTasks."""
    try:
        import sys
        sys.path.insert(0, ".")
        from agent.graph import build_graph
        graph  = build_graph()
        result = graph.invoke(lead_state)
        update_call(call_id, outcome=result.get("outcome", "unknown"))
    except Exception as exc:
        print(f"[VAPI] Agent error for call {call_id}: {exc}")
        update_call(call_id, outcome="agent_error")


async def handle_lookup_lead(args: dict) -> str:
    phone    = args.get("phone", "")
    customer = get_customer_by_phone(phone)
    if customer:
        return (
            f"Welcome back, {customer.get('name')}. "
            f"You have used our service {customer.get('total_jobs', 0)} time(s)."
        )
    return "This appears to be a new customer account."


async def handle_escalate_call(args: dict, call_id: str) -> str:
    phone  = args.get("lead_phone", "")
    name   = args.get("lead_name", "Unknown")
    reason = args.get("reason", "")
    try:
        from tools.twilio_tool import send_sms
        if BUSINESS_PHONE:
            send_sms(
                to=BUSINESS_PHONE,
                body=f"CALL ESCALATION: {name} ({phone}) needs manual callback. Reason: {reason}",
            )
    except Exception as exc:
        print(f"[VAPI] Escalation SMS failed: {exc}")
    update_call(call_id, outcome="escalated")
    return "I am flagging your request right now and one of our team members will call you back shortly."


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.post("/webhook")
async def vapi_webhook(request: Request, background_tasks: BackgroundTasks):
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    message  = body.get("message", {})
    msg_type = message.get("type", "")
    call     = message.get("call", {})
    call_id  = call.get("id", "unknown")

    # ── Call started ──────────────────────────────────────────────────────────
    if msg_type == "call-started":
        customer_number = call.get("customer", {}).get("number", "")
        direction       = call.get("type", "inboundPhoneCall")
        log_call(
            call_id,
            phone=customer_number,
            direction="inbound" if "inbound" in direction.lower() else "outbound",
        )
        return JSONResponse({"status": "logged"})

    # ── Function call ─────────────────────────────────────────────────────────
    if msg_type == "function-call":
        func_call = message.get("functionCall", {})
        func_name = func_call.get("name", "")
        func_args = func_call.get("parameters", {})

        if func_name == "qualify_lead":
            result = await handle_qualify_lead(func_args, call_id, background_tasks)
        elif func_name == "lookup_lead":
            result = await handle_lookup_lead(func_args)
        elif func_name == "escalate_call":
            result = await handle_escalate_call(func_args, call_id)
        else:
            result = f"Function {func_name} not implemented."

        return JSONResponse({"result": result})

    # ── End of call ───────────────────────────────────────────────────────────
    if msg_type == "end-of-call-report":
        # ✅ Parse ISO timestamps correctly
        duration_sec = _parse_duration(
            call.get("startedAt", ""),
            call.get("endedAt", ""),
        )
        transcript = message.get("transcript", "")
        update_call(
            call_id,
            duration_sec=duration_sec,
            full_transcript=transcript,
            transcript_preview=transcript[:200] if transcript else "",
        )
        # ✅ Twilio force-hangup removed — call already ended here
        return JSONResponse({"status": "logged"})

    return JSONResponse({"status": "ignored", "type": msg_type})


# ── Outbound call ─────────────────────────────────────────────────────────────

class OutboundCallRequest(BaseModel):
    phone:        str
    lead_name:    str
    service_type: str
    urgency:      str = "routine"
    followup_num: int = 1
    booking_url:  str = ""


@router.post("/outbound")
async def trigger_outbound_call(req: OutboundCallRequest):
    if not VAPI_API_KEY:
        raise HTTPException(status_code=503, detail="VAPI_API_KEY not configured")
    if not VAPI_PHONE_NUMBER_ID:
        raise HTTPException(status_code=503, detail="VAPI_PHONE_NUMBER_ID not configured")

    payload = {
        "assistantId":   VAPI_ASSISTANT_ID,
        "phoneNumberId": VAPI_PHONE_NUMBER_ID,
        "customer":      {"number": req.phone, "name": req.lead_name},
        "assistantOverrides": {
            "firstMessage": (
                f"Hi, may I speak with {req.lead_name}? "
                f"This is calling from {BUSINESS_NAME} regarding your HVAC service request."
            )
        },
    }

    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            f"{VAPI_BASE_URL}/call/phone",
            json=payload,
            headers={
                "Authorization": f"Bearer {VAPI_API_KEY}",
                "Content-Type":  "application/json",
            },
        )

    if response.status_code not in (200, 201):
        raise HTTPException(
            status_code=response.status_code,
            detail=f"Vapi error: {response.text}",
        )

    call_data = response.json()
    call_id   = call_data.get("id", "unknown")
    log_call(call_id, lead_name=req.lead_name, phone=req.phone,
             direction="outbound", outcome="pending")
    return {"call_id": call_id, "status": "dialing", "phone": req.phone}


# ── List calls ────────────────────────────────────────────────────────────────

@router.get("/calls")
async def list_calls(limit: int = 50):
    with get_conn(row_factory=dict_row) as conn:
        rows = conn.execute(
            "SELECT * FROM voice_calls ORDER BY created_at DESC LIMIT %s", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]