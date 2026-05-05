"""
booking/booking_handler.py
Calendly webhook + full confirmation pipeline.

Fixes applied:
  - SQLite fully replaced with PostgreSQL (psycopg3)
  - ensure_bookings_table() removed from hot path
  - send_appointment_reminders() uses PostgreSQL
  - All DB operations use get_conn() from db.postgres
"""

import hashlib
import hmac
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from dotenv import load_dotenv
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel

from db.postgres import get_conn

load_dotenv()

router = APIRouter()

CALENDLY_WEBHOOK_SECRET = os.getenv("CALENDLY_WEBHOOK_SECRET", "")
BUSINESS_NAME           = os.getenv("BUSINESS_NAME", "HVAC Pro")
BUSINESS_PHONE          = os.getenv("BUSINESS_PHONE", "")
BUSINESS_EMAIL          = os.getenv("BUSINESS_EMAIL", "")


# ── Datetime helpers ──────────────────────────────────────────────────────────

def _format_dt(dt: datetime) -> str:
    hour   = dt.hour % 12 or 12
    am_pm  = "AM" if dt.hour < 12 else "PM"
    return dt.strftime(f"%A, %B %d at {hour}:%M {am_pm}")


def _format_dt_short(dt: datetime) -> str:
    hour  = dt.hour % 12 or 12
    am_pm = "AM" if dt.hour < 12 else "PM"
    return dt.strftime(f"%A at {hour}:%M {am_pm}")


# ── DB helpers (PostgreSQL) ───────────────────────────────────────────────────

def save_booking(data: dict) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO bookings
                (calendly_event_id, lead_phone, lead_name, lead_email,
                 service_type, urgency, scheduled_at, status, calendly_payload)
            VALUES (%s, %s, %s, %s, %s, %s, %s, 'confirmed', %s)
            ON CONFLICT (calendly_event_id) DO UPDATE SET
                status           = 'confirmed',
                calendly_payload = EXCLUDED.calendly_payload,
                updated_at       = NOW()
            """,
            (
                data.get("event_id"),
                data.get("phone"),
                data.get("name"),
                data.get("email"),
                data.get("service_type", "HVAC service"),
                data.get("urgency", "routine"),
                data.get("scheduled_at"),
                data.get("raw_payload", "")[:2000],
            ),
        )
        conn.execute(
            """
            INSERT INTO appointments
                (lead_name, phone, email, service_type, scheduled_at, status, client_id)
            VALUES (%s, %s, %s, %s, %s, 'scheduled', %s)
            """,
            (
                data.get("name", ""),
                data.get("phone", ""),
                data.get("email", ""),
                data.get("service_type", "HVAC service"),
                data.get("scheduled_at"),
                data.get("client_id", 1),
            ),
        )
        conn.commit()


def update_booking_status(event_id: str, status: str, reason: str = "") -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE bookings
            SET status = %s, cancellation_reason = %s, updated_at = NOW()
            WHERE calendly_event_id = %s
            """,
            (status, reason, event_id),
        )
        conn.commit()


def get_lead_by_phone(phone: str) -> Optional[dict]:
    with get_conn(row_factory=dict_row) as conn:
        row = conn.execute(
            "SELECT * FROM leads WHERE phone = %s ORDER BY created_at DESC LIMIT 1",
            (phone,),
        ).fetchone()
        return dict(row) if row else None


def update_lead_booking_confirmed(phone: str) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            UPDATE leads
            SET outcome = 'booked', booking_confirmed = TRUE, updated_at = NOW()
            WHERE phone = %s
            """,
            (phone,),
        )
        conn.commit()


# ── Signature verification ────────────────────────────────────────────────────

def verify_calendly_signature(payload: bytes, signature_header: str) -> bool:
    if not CALENDLY_WEBHOOK_SECRET:
        print("[BOOKING] WARNING: No webhook secret — skipping verification")
        return True
    try:
        parts = dict(p.split("=", 1) for p in signature_header.split(","))
        ts    = parts.get("t", "")
        sig   = parts.get("v1", "")
        to_sign  = f"{ts}.{payload.decode()}"
        expected = hmac.new(
            CALENDLY_WEBHOOK_SECRET.encode(),
            to_sign.encode(),
            hashlib.sha256,
        ).hexdigest()
        return hmac.compare_digest(expected, sig)
    except Exception as exc:
        print(f"[BOOKING] Signature verification error: {exc}")
        return False


# ── Confirmation pipeline ─────────────────────────────────────────────────────

async def process_confirmed_booking(booking_data: dict) -> None:
    phone        = booking_data.get("phone", "")
    name         = booking_data.get("name", "Customer")
    email        = booking_data.get("email", "")
    scheduled_at = booking_data.get("scheduled_at", "")
    service_type = booking_data.get("service_type", "HVAC service")
    urgency      = booking_data.get("urgency", "routine")

    print(f"[BOOKING] Processing confirmation for {name} ({phone}) | {scheduled_at}")

    save_booking(booking_data)
    if phone:
        update_lead_booking_confirmed(phone)

    try:
        dt           = datetime.fromisoformat(scheduled_at.replace("Z", "+00:00"))
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


def _send_confirmation_sms(phone: str, name: str, service: str, display_time: str) -> None:
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
    except Exception as exc:
        print(f"[BOOKING] SMS error: {exc}")


def _send_confirmation_email(name: str, email: str, service: str,
                              display_time: str, urgency: str) -> None:
    try:
        from tools.sendgrid_tool import send_email
        html = f"""
<div style="font-family:Arial,sans-serif;max-width:600px;">
  <h2 style="color:#1a56db;">Appointment Confirmed ✅</h2>
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
        send_email(
            to=email,
            subject=f"Appointment Confirmed — {BUSINESS_NAME}",
            html_content=html,
        )
        print(f"[BOOKING] Confirmation email sent to {email}")
    except Exception as exc:
        print(f"[BOOKING] Email error: {exc}")


def _create_calendar_event(booking_data: dict, scheduled_at: str, display_time: str) -> None:
    try:
        from mcp.calendar_mcp import create_job_event
        state = {
            "lead_name":         booking_data.get("name", ""),
            "lead_phone":        booking_data.get("phone", ""),
            "lead_address":      booking_data.get("address", ""),
            "lead_service_type": booking_data.get("service_type", "HVAC"),
            "lead_urgency":      booking_data.get("urgency", "routine"),
        }
        create_job_event(state, confirmed_time=display_time)
        print("[BOOKING] Calendar event created")
    except Exception as exc:
        print(f"[BOOKING] Calendar MCP error: {exc}")


def _update_hubspot(phone: str, name: str) -> None:
    try:
        from mcp.hubspot_mcp import update_deal_stage
        update_deal_stage({"lead_phone": phone, "lead_name": name, "outcome": "booked"})
        print("[BOOKING] HubSpot deal updated → qualifiedtobuy")
    except Exception as exc:
        print(f"[BOOKING] HubSpot error: {exc}")


def _alert_team(booking_data: dict, display_time: str) -> None:
    name    = booking_data.get("name", "Customer")
    phone   = booking_data.get("phone", "")
    address = booking_data.get("address", "")
    service = booking_data.get("service_type", "HVAC")
    urgency = booking_data.get("urgency", "routine")

    try:
        from tools.twilio_tool import send_sms
        if BUSINESS_PHONE:
            send_sms(
                to=BUSINESS_PHONE,
                body=f"NEW BOOKING: {name} | {service} | {display_time} | {phone} | {address}",
            )
    except Exception as exc:
        print(f"[BOOKING] Team SMS error: {exc}")

    try:
        from mcp.gmail_mcp import send_team_alert
        send_team_alert(
            state={
                "lead_name":         name,
                "lead_phone":        phone,
                "lead_address":      address,
                "lead_service_type": service,
                "lead_urgency":      urgency,
            },
            outcome="booked",
            tech_briefing_text=f"Scheduled: {display_time}",
        )
    except Exception as exc:
        print(f"[BOOKING] Team email error: {exc}")


# ── Webhook ───────────────────────────────────────────────────────────────────

@router.post("/calendly-webhook")
async def handle_calendly_webhook(request: Request, background_tasks: BackgroundTasks):
    raw_body   = await request.body()
    sig_header = request.headers.get("Calendly-Webhook-Signature", "")

    if CALENDLY_WEBHOOK_SECRET and not verify_calendly_signature(raw_body, sig_header):
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    event_type = payload.get("event", "")
    data       = payload.get("payload", {})
    print(f"[BOOKING] Calendly event: {event_type}")

    if event_type == "invitee.created":
        invitee    = data.get("invitee", {})
        event_info = data.get("event", {})

        phone = ""
        for q in invitee.get("questions_and_answers", []):
            if "phone" in q.get("question", "").lower():
                phone = q.get("answer", "").strip()
                break

        booking_data = {
            "event_id":    event_info.get("uri", "").split("/")[-1],
            "name":        invitee.get("name", "Customer"),
            "email":       invitee.get("email", ""),
            "phone":       phone,
            "scheduled_at": event_info.get("start_time", ""),
            "service_type": _extract_service_from_payload(invitee),
            "urgency":     _extract_urgency_from_payload(invitee),
            "address":     _extract_address_from_payload(invitee),
            "raw_payload": str(payload)[:2000],
        }

        if phone:
            lead = get_lead_by_phone(phone)
            if lead:
                booking_data["service_type"] = booking_data["service_type"] or lead.get("service_type", "")
                booking_data["urgency"]       = booking_data["urgency"]       or lead.get("urgency", "routine")
                booking_data["address"]       = booking_data["address"]       or lead.get("address", "")
                booking_data["client_id"]     = lead.get("client_id", 1)

        background_tasks.add_task(process_confirmed_booking, booking_data)
        return JSONResponse({"status": "processing", "event": event_type})

    elif event_type == "invitee.canceled":
        invitee    = data.get("invitee", {})
        event_info = data.get("event", {})
        event_id   = event_info.get("uri", "").split("/")[-1]
        reason     = data.get("cancellation", {}).get("reason", "No reason given")
        update_booking_status(event_id, "cancelled", reason)
        _handle_cancellation(invitee, reason)
        return JSONResponse({"status": "cancelled", "event_id": event_id})

    return JSONResponse({"status": "ignored", "event": event_type})


# ── Payload extractors ────────────────────────────────────────────────────────

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


def _handle_cancellation(invitee: dict, reason: str) -> None:
    name  = invitee.get("name", "Customer")
    phone = ""
    for q in invitee.get("questions_and_answers", []):
        if "phone" in q.get("question", "").lower():
            phone = q.get("answer", "")
            break

    print(f"[BOOKING] Cancellation: {name} | reason: {reason}")
    try:
        from tools.twilio_tool import send_sms
        if BUSINESS_PHONE:
            send_sms(
                to=BUSINESS_PHONE,
                body=f"CANCELLATION: {name} ({phone}) | Reason: {reason[:80]}",
            )
        if phone:
            send_sms(
                to=phone,
                body=(
                    f"Hi {name.split()[0]}! We see you cancelled your HVAC appointment. "
                    f"We would still love to help — call {BUSINESS_PHONE} or reply here to reschedule."
                ),
            )
    except Exception as exc:
        print(f"[BOOKING] Cancellation SMS error: {exc}")


# ── Scheduler job (called every 30 min from main.py) ─────────────────────────

def send_appointment_reminders() -> None:
    """Called by APScheduler every 30 minutes. Uses PostgreSQL."""
    now = datetime.now(timezone.utc)

    try:
        with get_conn(row_factory=dict_row) as conn:
            # 24-hour reminders
            rows_24h = conn.execute(
                """
                SELECT * FROM bookings
                WHERE status = 'confirmed'
                  AND scheduled_at BETWEEN %s AND %s
                """,
                (now + timedelta(hours=23), now + timedelta(hours=25)),
            ).fetchall()
            for row in rows_24h:
                _send_reminder(dict(row), hours_before=24)
                conn.execute(
                    "UPDATE bookings SET status = 'reminder_sent', updated_at = NOW() WHERE id = %s",
                    (row["id"],),
                )

            # 2-hour reminders
            rows_2h = conn.execute(
                """
                SELECT * FROM bookings
                WHERE status IN ('confirmed', 'reminder_sent')
                  AND scheduled_at BETWEEN %s AND %s
                """,
                (now + timedelta(hours=1, minutes=45), now + timedelta(hours=2, minutes=15)),
            ).fetchall()
            for row in rows_2h:
                _send_reminder(dict(row), hours_before=2)
                conn.execute(
                    "UPDATE bookings SET status = 'reminder_2h', updated_at = NOW() WHERE id = %s",
                    (row["id"],),
                )

            conn.commit()
            print(f"[SCHEDULER] Reminders sent: {len(rows_24h)} x 24h, {len(rows_2h)} x 2h")
    except Exception as exc:
        print(f"[SCHEDULER] Reminder job error: {exc}")


def _send_reminder(booking: dict, hours_before: int) -> None:
    phone = booking.get("lead_phone", "")
    name  = (booking.get("lead_name", "Customer") or "Customer").split()[0]
    sched = booking.get("scheduled_at")

    try:
        if isinstance(sched, str):
            dt = datetime.fromisoformat(sched.replace("Z", "+00:00"))
        else:
            dt = sched
        display_time = _format_dt_short(dt)
    except Exception:
        display_time = str(sched)

    if hours_before == 24:
        body = (
            f"Reminder: Your HVAC appointment with {BUSINESS_NAME} is TOMORROW "
            f"({display_time}). Reply CONFIRM to confirm or call {BUSINESS_PHONE} to reschedule."
        )
    else:
        body = (
            f"Heads up {name}! Your HVAC tech arrives in about 2 hours ({display_time}). "
            f"Please make sure someone is home. Questions? {BUSINESS_PHONE}"
        )

    try:
        from tools.twilio_tool import send_sms
        send_sms(to=phone, body=body)
        print(f"[BOOKING] {hours_before}h reminder sent to {phone}")
    except Exception as exc:
        print(f"[BOOKING] Reminder SMS error: {exc}")


# ── Extra endpoints ───────────────────────────────────────────────────────────

@router.get("/upcoming")
async def get_upcoming_bookings(days: int = 7):
    end_date = datetime.now(timezone.utc) + timedelta(days=days)
    with get_conn(row_factory=dict_row) as conn:
        rows = conn.execute(
            """
            SELECT lead_name, lead_phone, service_type, urgency,
                   scheduled_at, status, technician
            FROM bookings
            WHERE status NOT IN ('cancelled', 'completed')
              AND scheduled_at >= NOW()
              AND scheduled_at <= %s
            ORDER BY scheduled_at ASC
            """,
            (end_date,),
        ).fetchall()
    return [dict(r) for r in rows]


@router.get("/stats")
async def get_booking_stats():
    with get_conn() as conn:
        total     = conn.execute("SELECT COUNT(*) FROM bookings").fetchone()[0]
        confirmed = conn.execute("SELECT COUNT(*) FROM bookings WHERE status != 'cancelled'").fetchone()[0]
        cancelled = conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'cancelled'").fetchone()[0]
        completed = conn.execute("SELECT COUNT(*) FROM bookings WHERE status = 'completed'").fetchone()[0]
    return {
        "total_bookings":   total,
        "confirmed":        confirmed,
        "cancelled":        cancelled,
        "completed":        completed,
        "cancel_rate":      round(cancelled / max(total, 1) * 100, 1),
        "completion_rate":  round(completed / max(total, 1) * 100, 1),
    }


class ManualBooking(BaseModel):
    lead_phone:   str
    lead_name:    str
    lead_email:   str = ""
    service_type: str
    urgency:      str = "routine"
    scheduled_at: str
    address:      str = ""


@router.post("/confirm-manual")
async def confirm_manual_booking(booking: ManualBooking, background_tasks: BackgroundTasks):
    booking_data = {
        "event_id":    f"manual_{booking.lead_phone}_{int(datetime.now(timezone.utc).timestamp())}",
        "name":        booking.lead_name,
        "email":       booking.lead_email,
        "phone":       booking.lead_phone,
        "service_type": booking.service_type,
        "urgency":     booking.urgency,
        "scheduled_at": booking.scheduled_at,
        "address":     booking.address,
        "raw_payload": "manual_entry",
    }
    background_tasks.add_task(process_confirmed_booking, booking_data)
    return {"status": "processing", "message": "Manual booking confirmation triggered"}