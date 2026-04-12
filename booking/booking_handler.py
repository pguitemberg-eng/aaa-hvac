"""
booking/booking_handler.py
Calendly webhook + full confirmation pipeline.
Windows-compatible version (no %-I strftime format).

Calendly setup:
  calendly.com/integrations/webhooks -> Create Webhook
  URL: https://yourdomain.com/booking/calendly-webhook
  Events: invitee.created, invitee.canceled
  Copy signing key -> CALENDLY_WEBHOOK_SECRET in .env
"""

import os
import sqlite3
import hashlib
import hmac
import asyncio
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

router = APIRouter()

CALENDLY_WEBHOOK_SECRET = os.getenv("CALENDLY_WEBHOOK_SECRET", "")
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "HVAC Pro")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "")
BUSINESS_EMAIL = os.getenv("BUSINESS_EMAIL", "")
DB_PATH = os.getenv("SQLITE_DB_PATH", "memory/hvac_leads.db")


def _format_dt(dt: datetime) -> str:
    """Format datetime without Linux-only %-I format code."""
    hour = dt.hour % 12 or 12
    am_pm = "AM" if dt.hour < 12 else "PM"
    return dt.strftime(f"%A, %B %d at {hour}:%M {am_pm}")


def _format_dt_short(dt: datetime) -> str:
    hour = dt.hour % 12 or 12
    am_pm = "AM" if dt.hour < 12 else "PM"
    return dt.strftime(f"%A at {hour}:%M {am_pm}")


def ensure_bookings_table():
    conn = sqlite3.connect(DB_PATH)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS bookings (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            calendly_event_id TEXT UNIQUE,
            lead_phone TEXT,
            lead_name TEXT,
            lead_email TEXT,
            service_type TEXT,
            urgency TEXT DEFAULT 'routine',
            scheduled_at TEXT,
            duration_min INTEGER DEFAULT 120,
            technician TEXT,
            status TEXT DEFAULT 'confirmed',
            cancellation_reason TEXT,
            calendly_payload TEXT,
            confirmed_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    conn.close()


def save_booking(data: dict) -> int:
    conn = sqlite3.connect(DB_PATH)
    cur = conn.execute(
        """INSERT OR REPLACE INTO bookings
           (calendly_event_id, lead_phone, lead_name, lead_email,
            service_type, urgency, scheduled_at, status, calendly_payload)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("event_id"),
            data.get("phone"),
            data.get("name"),
            data.get("email"),
            data.get("service_type", "HVAC service"),
            data.get("urgency", "routine"),
            data.get("scheduled_at"),
            "confirmed",
            data.get("raw_payload", ""),
        ),
    )
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def update_booking_status(event_id: str, status: str, reason: str = ""):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE bookings SET status = ?, cancellation_reason = ?, updated_at = datetime('now') WHERE calendly_event_id = ?",
        (status, reason, event_id),
    )
    conn.commit()
    conn.close()


def get_lead_by_phone(phone: str) -> Optional[dict]:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM leads WHERE phone = ? ORDER BY created_at DESC LIMIT 1",
        (phone,),
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_lead_booking_confirmed(phone: str, booking_time: str):
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        "UPDATE leads SET outcome = 'booked', booking_confirmed = 1, updated_at = datetime('now') WHERE phone = ?",
        (phone,),
    )
    conn.commit()
    conn.close()


def verify_calendly_signature(payload: bytes, signature_header: str) -> bool:
    if not CALENDLY_WEBHOOK_SECRET:
        print("[BOOKING] WARNING: No webhook secret - skipping verification")
        return True
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts = parts.get("t", "")
        sig = parts.get("v1", "")
        to_sign = f"{ts}.{payload.decode()}"
        expected = hmac.new(
            CALENDLY_WEBHOOK_SECRET.encode(),
            to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception as e:
        print(f"[BOOKING] Signature verification error: {e}")
        return False


async def process_confirmed_booking(booking_data: dict):
    phone = booking_data.get("phone", "")
    name = booking_data.get("name", "Customer")
    email = booking_data.get("email", "")
    scheduled_at = booking_data.get("scheduled_at", "")
    service_type = booking_data.get("service_type", "HVAC service")
    urgency = booking_data.get("urgency", "routine")

    print(f"[BOOKING] Processing confirmation for {name} ({phone}) | {scheduled_at}")

    save_booking(booking_data)
    if phone:
        update_lead_booking_confirmed(phone, scheduled_at)

    try:
        dt = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
        display_time = _format_dt(dt)
    except Exception:
        display_time = scheduled_at

    _send_confirmation_sms(phone, name, service_type, display_time)
    if email:
        _send_confirmation_email(name, email, service_type, display_time, urgency)
    _create_calendar_event(booking_data, scheduled_at, display_time)
    _update_hubspot(phone, name)
    _alert_team(booking_data, display_time)

    print(f"[BOOKING] Full confirmation pipeline done for {name}")


def _send_confirmation_sms(phone: str, name: str, service: str, display_time: str):
    if not phone:
        return
    try:
        from tools.twilio_tool import send_sms
        body = (
            f"Booking confirmed, {name}. "
            f"Your {service} appointment is set for {display_time}. "
            f"Our tech will arrive within the scheduled window. "
            f"Questions? Call {BUSINESS_PHONE}."
        )
        send_sms(to=phone, body=body)
        print(f"[BOOKING] Confirmation SMS sent to {phone}")
    except Exception as e:
        print(f"[BOOKING] SMS error: {e}")


def _send_confirmation_email(name: str, email: str, service: str,
                              display_time: str, urgency: str):
    try:
        from mcp.mcp_client import call_mcp
        prompt = f"""
Send a booking confirmation email via Gmail:

TO: {email}
SUBJECT: Appointment Confirmed - {BUSINESS_NAME}

Write a professional confirmation email:
- Customer: {name}
- Service: {service}
- Appointment: {display_time}
- What to expect: Technician will arrive in the scheduled window, perform a diagnostic, and provide a quote before any work
- What to have ready: Easy access to HVAC unit (basement, attic, or outside)
- Contact: {BUSINESS_PHONE}
- Business: {BUSINESS_NAME}

Keep it warm, professional, and under 150 words.
Send the email and confirm delivery.
""".strip()
        call_mcp(prompt=prompt, server_key="gmail")
        print(f"[BOOKING] Confirmation email sent to {email}")
    except Exception as e:
        try:
            from tools.sendgrid_tool import send_email
            html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;">
  <h2 style="color:#1a56db;">Appointment Confirmed</h2>
  <p>Hi <strong>{name}</strong>,</p>
  <p>Your <strong>{service}</strong> appointment is confirmed for:</p>
  <div style="background:#f0f7ff;padding:16px;border-radius:8px;
              border-left:4px solid #1a56db;font-size:18px;font-weight:bold;">
    {display_time}
  </div>
  <p>Our technician will perform a full diagnostic before starting any work.</p>
  <p><strong>Please have ready:</strong> Access to your HVAC unit</p>
  <p>Questions? Call: <a href="tel:{BUSINESS_PHONE}">{BUSINESS_PHONE}</a></p>
  <p>The {BUSINESS_NAME} Team</p>
</div>
"""
            send_email(to=email, subject=f"Appointment Confirmed - {BUSINESS_NAME}", html_content=html)
        except Exception as e2:
            print(f"[BOOKING] Email fallback failed: {e2}")


def _create_calendar_event(booking_data: dict, scheduled_at: str, display_time: str):
    try:
        from mcp.calendar_mcp import create_job_event
        state = {
            "lead_name": booking_data.get("name", ""),
            "lead_phone": booking_data.get("phone", ""),
            "lead_address": booking_data.get("address", ""),
            "lead_service_type": booking_data.get("service_type", "HVAC"),
            "lead_urgency": booking_data.get("urgency", "routine"),
        }
        create_job_event(state, confirmed_time=display_time)
        print(f"[BOOKING] Calendar event created")
    except Exception as e:
        print(f"[BOOKING] Calendar MCP error: {e}")


def _update_hubspot(phone: str, name: str):
    try:
        from mcp.hubspot_mcp import update_deal_stage
        update_deal_stage({"lead_phone": phone, "lead_name": name, "outcome": "booked"})
        print(f"[BOOKING] HubSpot deal updated -> qualifiedtobuy")
    except Exception as e:
        print(f"[BOOKING] HubSpot error: {e}")


def _alert_team(booking_data: dict, display_time: str):
    name = booking_data.get("name", "Customer")
    phone = booking_data.get("phone", "")
    address = booking_data.get("address", "")
    service = booking_data.get("service_type", "HVAC")
    urgency = booking_data.get("urgency", "routine")

    try:
        from tools.twilio_tool import send_sms
        if BUSINESS_PHONE:
            send_sms(to=BUSINESS_PHONE, body=f"NEW BOOKING: {name} | {service} | {display_time} | {phone} | {address}")
    except Exception as e:
        print(f"[BOOKING] Team SMS error: {e}")

    try:
        from mcp.gmail_mcp import send_team_alert
        state = {
            "lead_name": name,
            "lead_phone": phone,
            "lead_address": address,
            "lead_service_type": service,
            "lead_urgency": urgency,
        }
        send_team_alert(state=state, outcome="booked", tech_briefing_text=f"Scheduled: {display_time}")
    except Exception as e:
        print(f"[BOOKING] Team email error: {e}")


@router.post("/calendly-webhook")
async def handle_calendly_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body = await request.body()
    sig_header = request.headers.get("Calendly-Webhook-Signature", "")

    if CALENDLY_WEBHOOK_SECRET and not verify_calendly_signature(raw_body, sig_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    ensure_bookings_table()

    event_type = payload.get("event", "")
    data = payload.get("payload", {})
    print(f"[BOOKING] Calendly event: {event_type}")

    if event_type == "invitee.created":
        invitee = data.get("invitee", {})
        event_info = data.get("event", {})

        phone = ""
        for q in invitee.get("questions_and_answers", []):
            if "phone" in q.get("question", "").lower():
                phone = q.get("answer", "").strip()
                break

        booking_data = {
            "event_id": event_info.get("uri", "").split("/")[-1],
            "name": invitee.get("name", "Customer"),
            "email": invitee.get("email", ""),
            "phone": phone,
            "scheduled_at": event_info.get("start_time", ""),
            "service_type": _extract_service_from_payload(invitee),
            "urgency": _extract_urgency_from_payload(invitee),
            "address": _extract_address_from_payload(invitee),
            "raw_payload": str(payload)[:2000],
        }

        if phone:
            lead = get_lead_by_phone(phone)
            if lead:
                booking_data["service_type"] = booking_data["service_type"] or lead.get("service_type", "")
                booking_data["urgency"] = booking_data["urgency"] or lead.get("urgency", "routine")
                booking_data["address"] = booking_data["address"] or lead.get("address", "")

        background_tasks.add_task(process_confirmed_booking, booking_data)
        return JSONResponse({"status": "processing", "event": event_type})

    elif event_type == "invitee.canceled":
        invitee = data.get("invitee", {})
        event_info = data.get("event", {})
        event_id = event_info.get("uri", "").split("/")[-1]
        reason = data.get("cancellation", {}).get("reason", "No reason given")
        update_booking_status(event_id, "cancelled", reason)
        _handle_cancellation(invitee, reason)
        return JSONResponse({"status": "cancelled", "event_id": event_id})

    return JSONResponse({"status": "ignored", "event": event_type})


def _extract_service_from_payload(invitee: dict) -> str:
    for q in invitee.get("questions_and_answers", []):
        if any(w in q.get("question", "").lower() for w in ["service", "problem", "issue", "hvac", "describe"]):
            return q.get("answer", "")
    return ""


def _extract_urgency_from_payload(invitee: dict) -> str:
    for q in invitee.get("questions_and_answers", []):
        if "urgent" in q.get("question", "").lower():
            answer = q.get("answer", "").lower()
            if "emergency" in answer:
                return "emergency"
            if "urgent" in answer:
                return "urgent"
    return "routine"


def _extract_address_from_payload(invitee: dict) -> str:
    for q in invitee.get("questions_and_answers", []):
        if "address" in q.get("question", "").lower():
            return q.get("answer", "")
    return ""


def _handle_cancellation(invitee: dict, reason: str):
    name = invitee.get("name", "Customer")
    phone = ""
    for q in invitee.get("questions_and_answers", []):
        if "phone" in q.get("question", "").lower():
            phone = q.get("answer", "")
            break

    print(f"[BOOKING] Cancellation: {name} | reason: {reason}")

    try:
        from tools.twilio_tool import send_sms
        if BUSINESS_PHONE:
            send_sms(to=BUSINESS_PHONE, body=f"CANCELLATION: {name} ({phone}) | Reason: {reason[:80]}")
        if phone:
            send_sms(to=phone, body=f"Hi {name.split()[0]}! We see you cancelled your HVAC appointment. We would still love to help - call {BUSINESS_PHONE} or reply here to reschedule.")
    except Exception as e:
        print(f"[BOOKING] Cancellation SMS error: {e}")


def send_appointment_reminders():
    """Called by scheduler every 30 minutes."""
    ensure_bookings_table()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    now = datetime.utcnow()

    rows_24h = conn.execute(
        """SELECT * FROM bookings WHERE status = 'confirmed'
           AND scheduled_at BETWEEN ? AND ?""",
        ((now + timedelta(hours=23)).isoformat(), (now + timedelta(hours=25)).isoformat()),
    ).fetchall()
    for row in rows_24h:
        _send_reminder(dict(row), hours_before=24)
        conn.execute("UPDATE bookings SET status = 'reminder_sent', updated_at = datetime('now') WHERE id = ?", (row["id"],))

    rows_2h = conn.execute(
        """SELECT * FROM bookings WHERE status IN ('confirmed', 'reminder_sent')
           AND scheduled_at BETWEEN ? AND ?""",
        ((now + timedelta(hours=1, minutes=45)).isoformat(), (now + timedelta(hours=2, minutes=15)).isoformat()),
    ).fetchall()
    for row in rows_2h:
        _send_reminder(dict(row), hours_before=2)
        conn.execute("UPDATE bookings SET status = 'reminder_2h', updated_at = datetime('now') WHERE id = ?", (row["id"],))

    conn.commit()
    conn.close()


def _send_reminder(booking: dict, hours_before: int):
    phone = booking.get("lead_phone", "")
    name = booking.get("lead_name", "Customer").split()[0]
    sched = booking.get("scheduled_at", "")

    try:
        dt = datetime.fromisoformat(sched.replace("Z", "+00:00"))
        display_time = _format_dt_short(dt)
    except Exception:
        display_time = sched

    if hours_before == 24:
        body = f"Reminder: Your HVAC appointment with {BUSINESS_NAME} is TOMORROW ({display_time}). Reply CONFIRM to confirm or call {BUSINESS_PHONE} to reschedule."
    else:
        body = f"Heads up {name}! Your HVAC tech arrives in about 2 hours ({display_time}). Please make sure someone is home. Questions? {BUSINESS_PHONE}"

    try:
        from tools.twilio_tool import send_sms
        send_sms(to=phone, body=body)
        print(f"[BOOKING] {hours_before}h reminder sent to {phone}")
    except Exception as e:
        print(f"[BOOKING] Reminder SMS error: {e}")


@router.get("/upcoming")
async def get_upcoming_bookings(days: int = 7):
    ensure_bookings_table()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    end_date = (datetime.utcnow() + timedelta(days=days)).isoformat()
    rows = conn.execute(
        """SELECT lead_name, lead_phone, service_type, urgency, scheduled_at, status, technician
           FROM bookings WHERE status NOT IN ('cancelled', 'completed')
           AND scheduled_at >= datetime('now') AND scheduled_at <= ?
           ORDER BY scheduled_at ASC""",
        (end_date,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


@router.get("/stats")
async def get_booking_stats():
    ensure_bookings_table()
    conn = sqlite3.connect(DB_PATH)
    total = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
    confirmed = conn.execute("SELECT COUNT(*) FROM bookings WHERE status != 'cancelled'").fetchone()[0]
    cancelled = conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'cancelled'").fetchone()[0]
    completed = conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'completed'").fetchone()[0]
    conn.close()
    return {
        "total_bookings": total,
        "confirmed": confirmed,
        "cancelled": cancelled,
        "completed": completed,
        "cancel_rate": round(cancelled / max(total, 1) * 100, 1),
        "completion_rate": round(completed / max(total, 1) * 100, 1),
    }


class ManualBooking(BaseModel):
    lead_phone: str
    lead_name: str
    lead_email: str = ""
    service_type: str
    urgency: str = "routine"
    scheduled_at: str
    address: str = ""


@router.post("/confirm-manual")
async def confirm_manual_booking(booking: ManualBooking, background_tasks: BackgroundTasks):
    booking_data = {
        "event_id": f"manual_{booking.lead_phone}_{int(datetime.utcnow().timestamp())}",
        "name": booking.lead_name,
        "email": booking.lead_email,
        "phone": booking.lead_phone,
        "service_type": booking.service_type,
        "urgency": booking.urgency,
        "scheduled_at": booking.scheduled_at,
        "address": booking.address,
        "raw_payload": "manual_entry",
    }
    background_tasks.add_task(process_confirmed_booking, booking_data)
    return {"status": "processing", "message": "Manual booking confirmation triggered"}