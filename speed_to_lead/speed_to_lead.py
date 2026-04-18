"""
speed_to_lead/speed_to_lead.py
Unified lead intake router. All sources converge here.
Fires acknowledgment SMS in < 5 seconds. Booking link delivered in < 60 seconds.
"""

import os
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel
from dotenv import load_dotenv

from db.postgres import get_conn

load_dotenv()

router = APIRouter()

BUSINESS_NAME = os.getenv("BUSINESS_NAME", "HVAC Pro")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "")

ACK_SMS = (
    "Hi {name}! {business} here. "
    "We got your request for {service}. "
    "Sending you a booking link right now."
)

ACK_SMS_NO_NAME = (
    "Hi! {business} here. "
    "We got your HVAC service request. "
    "Sending you a booking link right now."
)


class WebFormLead(BaseModel):
    name: str
    phone: str
    email: str = ""
    address: str = ""
    service_type: str = ""
    urgency: str = "routine"
    budget: str = ""
    message: str = ""
    source: str = "web_form"


class ManualLead(BaseModel):
    name: str
    phone: str
    email: str = ""
    address: str = ""
    service_type: str
    urgency: str = "routine"
    budget: str = ""
    message: str = ""


async def fire_speed_to_lead(lead: dict, background_tasks: BackgroundTasks):
    phone = lead.get("lead_phone", "")
    name = lead.get("lead_name", "")
    service = lead.get("lead_service_type", "HVAC service")

    print(f"[SPEED_TO_LEAD] Firing for {name} ({phone}) | {service}")

    background_tasks.add_task(_send_ack_sms, phone, name, service)
    background_tasks.add_task(_run_full_pipeline, lead)
    _log_lead_intake(phone, name, lead.get("source", "unknown"))

    return {
        "status": "processing",
        "message": f"Speed-to-lead fired for {name}. Ack SMS queued.",
        "phone": phone,
        "timestamp": datetime.utcnow().isoformat(),
    }


def _send_ack_sms(phone: str, name: str, service: str):
    if not phone:
        return

    first_name = name.split()[0] if name and name != "Missed Call" and name != "SMS Customer" else ""
    if first_name:
        body = ACK_SMS.format(
            name=first_name,
            business=BUSINESS_NAME,
            service=service or "your HVAC issue",
        )
    else:
        body = ACK_SMS_NO_NAME.format(business=BUSINESS_NAME)

    try:
        from tools.twilio_tool import send_sms
        send_sms(to=phone, body=body)
        print(f"[SPEED_TO_LEAD] Ack SMS sent to {phone}")
    except Exception as e:
        print(f"[SPEED_TO_LEAD] Ack SMS failed: {e}")


async def _run_full_pipeline(lead: dict):
    import sys
    sys.path.insert(0, ".")

    try:
        from langchain_core.messages import HumanMessage
        from agent.graph import build_graph

        message_content = lead.get("message") or lead.get("lead_service_type", "HVAC service needed")

        lead_state = {
            "messages": [HumanMessage(content=message_content)],
            "lead_name": lead.get("lead_name", ""),
            "lead_phone": lead.get("lead_phone", ""),
            "lead_email": lead.get("lead_email", ""),
            "lead_address": lead.get("lead_address", ""),
            "lead_service_type": lead.get("lead_service_type", ""),
            "lead_urgency": lead.get("lead_urgency", "routine"),
            "lead_budget": lead.get("lead_budget", ""),
            "followup_count": 0,
            "followup_max": 3,
            "booking_confirmed": False,
            "source": lead.get("source", "unknown"),
            "outcome": "",
            "error": "",
        }

        start_time = datetime.utcnow()
        graph = build_graph()
        result = graph.invoke(lead_state)
        elapsed = (datetime.utcnow() - start_time).total_seconds()

        outcome = result.get("outcome", "unknown")
        print(f"[SPEED_TO_LEAD] Pipeline done | {lead.get('lead_name')} | outcome={outcome} | elapsed={elapsed:.1f}s")
        _update_lead_timing(lead.get("lead_phone", ""), elapsed)

    except Exception as e:
        print(f"[SPEED_TO_LEAD] Pipeline error: {e}")


def _log_lead_intake(phone: str, name: str, source: str):
    with get_conn() as conn:
        conn.execute("""
        CREATE TABLE IF NOT EXISTS lead_timing (
            id BIGSERIAL PRIMARY KEY,
            phone TEXT,
            name TEXT,
            source TEXT,
            intake_at TIMESTAMPTZ DEFAULT NOW(),
            pipeline_sec REAL,
            ack_sent INTEGER DEFAULT 1
        )
    """)
        conn.execute(
            "INSERT INTO lead_timing (phone, name, source) VALUES (%s, %s, %s)",
            (phone, name, source),
        )
        conn.commit()


def _update_lead_timing(phone: str, elapsed_sec: float):
    with get_conn() as conn:
        conn.execute(
            """UPDATE lead_timing
               SET pipeline_sec = %s
               WHERE id = (
                   SELECT id FROM lead_timing
                   WHERE phone = %s
                   ORDER BY intake_at DESC
                   LIMIT 1
               )""",
            (elapsed_sec, phone),
        )
        conn.commit()


@router.post("/web-form")
async def intake_web_form(lead: WebFormLead, background_tasks: BackgroundTasks):
    lead_dict = {
        "lead_name": lead.name,
        "lead_phone": lead.phone,
        "lead_email": lead.email,
        "lead_address": lead.address,
        "lead_service_type": lead.service_type or lead.message,
        "lead_urgency": lead.urgency,
        "lead_budget": lead.budget,
        "message": lead.message,
        "source": "web_form",
    }
    return await fire_speed_to_lead(lead_dict, background_tasks)


@router.post("/sms-inbound")
async def intake_sms(request: Request, background_tasks: BackgroundTasks):
    form_data = await request.form()
    phone = str(form_data.get("From", ""))
    body = str(form_data.get("Body", "")).strip()

    if not phone or not body:
        return JSONResponse({"status": "ignored"})

    print(f"[SMS_INBOUND] From={phone} | Body={body[:80]}")

    urgency = _detect_urgency_from_text(body)

    lead_dict = {
        "lead_name": "SMS Customer",
        "lead_phone": phone,
        "lead_email": "",
        "lead_address": "",
        "lead_service_type": body,
        "lead_urgency": urgency,
        "lead_budget": "",
        "message": body,
        "source": "sms_inbound",
    }
    await fire_speed_to_lead(lead_dict, background_tasks)
    return Response(content="<Response/>", media_type="application/xml")


@router.post("/manual")
async def intake_manual(lead: ManualLead, background_tasks: BackgroundTasks):
    lead_dict = {
        "lead_name": lead.name,
        "lead_phone": lead.phone,
        "lead_email": lead.email,
        "lead_address": lead.address,
        "lead_service_type": lead.service_type,
        "lead_urgency": lead.urgency,
        "lead_budget": lead.budget,
        "message": lead.message,
        "source": "manual_entry",
    }
    return await fire_speed_to_lead(lead_dict, background_tasks)


@router.post("/facebook")
async def intake_facebook_lead(request: Request, background_tasks: BackgroundTasks):
    params = dict(request.query_params)
    if params.get("hub.mode") == "subscribe":
        verify_token = os.getenv("FACEBOOK_VERIFY_TOKEN", "")
        if params.get("hub.verify_token") == verify_token:
            return Response(content=params.get("hub.challenge", ""), media_type="text/plain")
        raise HTTPException(status_code=403, detail="Invalid verify token")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    for entry in body.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") == "leadgen":
                value = change.get("value", {})
                leadgen_id = value.get("leadgen_id")
                if leadgen_id:
                    background_tasks.add_task(_fetch_and_process_facebook_lead, leadgen_id)

    return JSONResponse({"status": "processing"})


async def _fetch_and_process_facebook_lead(leadgen_id: str):
    import httpx
    token = os.getenv("FACEBOOK_ACCESS_TOKEN", "")
    if not token:
        print(f"[FACEBOOK] No access token - cannot fetch lead {leadgen_id}")
        return

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                f"https://graph.facebook.com/v18.0/{leadgen_id}",
                params={"access_token": token},
                timeout=10,
            )
        data = resp.json()
        field_data = data.get("field_data", [])

        fields: dict = {}
        for f in field_data:
            name = f.get("name", "").lower()
            values = f.get("values", [])
            if values:
                fields[name] = values[0]

        phone = fields.get("phone_number", fields.get("phone", ""))
        name = fields.get("full_name", fields.get("name", "Facebook Lead"))
        email = fields.get("email", "")
        service = fields.get("hvac_service", fields.get("message", "HVAC service"))
        urgency = fields.get("urgency", "routine")

        lead_dict = {
            "lead_name": name,
            "lead_phone": phone,
            "lead_email": email,
            "lead_address": fields.get("street_address", ""),
            "lead_service_type": service,
            "lead_urgency": urgency,
            "lead_budget": "",
            "message": service,
            "source": "facebook_lead_ad",
        }

        _send_ack_sms(phone, name, service)
        await _run_full_pipeline(lead_dict)

    except Exception as e:
        print(f"[FACEBOOK] Error processing lead {leadgen_id}: {e}")


@router.get("/queue-status")
async def queue_status():
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT source, COUNT(*) as count,
                          AVG(pipeline_sec) as avg_sec,
                          MIN(pipeline_sec) as min_sec,
                          MAX(pipeline_sec) as max_sec
                   FROM lead_timing
                   WHERE intake_at > NOW() - INTERVAL '7 days'
                   GROUP BY source"""
            ).fetchall()
        return {
            "status": "ok",
            "sources": [
                {"source": r[0], "count": r[1],
                 "avg_sec": round(r[2] or 0, 1),
                 "min_sec": round(r[3] or 0, 1),
                 "max_sec": round(r[4] or 0, 1)}
                for r in rows
            ],
        }
    except Exception as e:
        return {"status": "error", "message": str(e)}


def _detect_urgency_from_text(text: str) -> str:
    text_lower = text.lower()
    if any(w in text_lower for w in [
        "emergency", "no heat", "no ac", "no cooling", "not working",
        "broken", "gas", "smoke", "flooding", "asap", "urgent", "now",
        "freezing", "hot", "burning",
    ]):
        return "urgent"
    return "routine"