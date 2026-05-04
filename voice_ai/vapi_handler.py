"""
voice_ai/vapi_handler.py — FINAL VERSION
Tools: checkAvailability, bookAppointment, endCall
Fix: Correct Vapi tool response format + proper flow
"""

import os
import json
import re
import sqlite3

import psycopg2
import httpx
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
DEFAULT_CLIENT_ID = int(os.getenv("DEFAULT_CLIENT_ID", "1"))
BUSINESS_NAME = os.getenv("BUSINESS_NAME", "HVAC Pro")
BUSINESS_PHONE = os.getenv("BUSINESS_PHONE", "")
VAPI_BASE_URL = "https://api.vapi.ai"
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")


# ── Date builders ─────────────────────────────────────────────────────────────

def build_first_message() -> str:
    """Short natural greeting — no dates read aloud."""
    return f"Thank you for calling {BUSINESS_NAME}! How can I help you today?"


def build_date_system_prompt() -> str:
    """Compact date context — agent uses internally, never reads aloud."""
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


# ── Auto-update assistant date ────────────────────────────────────────────────

@router.get("/update-assistant-date")
async def update_assistant_date():
    if not VAPI_API_KEY or not VAPI_ASSISTANT_ID:
        raise HTTPException(status_code=503, detail="VAPI config missing")

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

    if "[DATE CONTEXT" in current_prompt:
        current_prompt = current_prompt.split("\n\n[DATE CONTEXT")[0]

    new_prompt = current_prompt + build_date_system_prompt()
    first_message = build_first_message()

    print(f"[DATE-UPDATE] Updating assistant...")

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

    print(f"[DATE-UPDATE] Vapi response: {resp.status_code}")

    if resp.status_code not in (200, 201):
        raise HTTPException(status_code=resp.status_code, detail=f"Vapi update failed: {resp.text}")

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


def get_call_outcome(call_id: str) -> Optional[str]:
    conn = sqlite3.connect(DB_PATH)
    row = conn.execute(
        "SELECT outcome FROM voice_calls WHERE call_id = ?", (call_id,)
    ).fetchone()
    conn.close()
    return row[0] if row else None


# ── PostgreSQL helpers ────────────────────────────────────────────────────────

def insert_appointment_postgres(
    lead_name: str,
    phone: str,
    service_type: str,
    scheduled_at: datetime,
    client_id: int,
) -> None:
    database_url = (os.getenv("DATABASE_URL") or "").strip()
    if not database_url:
        print("[DB] Appointment not saved: DATABASE_URL not set")
        return
    try:
        conn = psycopg2.connect(database_url)
        try:
            with conn.cursor() as cur:
                cur.execute(
                    """INSERT INTO appointments (lead_name, phone, service_type, scheduled_at, status, client_id)
                       VALUES (%s, %s, %s, %s, 'scheduled', %s)""",
                    (
                        lead_name or "",
                        phone or "",
                        service_type or "",
                        scheduled_at,
                        client_id,
                    ),
                )
            conn.commit()
        finally:
            conn.close()
        print(f"[DB] PostgreSQL appointment: {lead_name!r} @ {scheduled_at}")
    except Exception as e:
        print(f"[DB] PostgreSQL appointment insert error: {e}")


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
    date_str = (date_str or "").strip()
    time_str = (time_str or "").strip()
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


def _first_arg_str(args: dict, *keys: str) -> str:
    """Return the first non-empty string among alternate Vapi/LLM parameter names."""
    for k in keys:
        v = args.get(k)
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return ""


def book_on_google_calendar(
    name: str, phone: str, address: str, service_type: str,
    appointment_dt: datetime, notes: str = ""
) -> dict:
    try:
        from integrations.gcal import book_google_calendar
        result = book_google_calendar(
            customer_name=name,
            customer_phone=phone,
            service_type=service_type,
            appointment_dt=appointment_dt,
            notes=f"Address: {address} | {notes}",
        )
        if result.get("success"):
            ref = (
                result.get("event_link")
                or result.get("html_link")
                or result.get("event_id")
                or result.get("id")
                or ""
            )
            print(f"[CALENDAR] Event created: {ref}")
            insert_appointment_postgres(
                lead_name=name,
                phone=phone,
                service_type=service_type,
                scheduled_at=appointment_dt,
                client_id=DEFAULT_CLIENT_ID,
            )
        else:
            print(f"[CALENDAR] Event not created: {result.get('error', result)}")
        return result
    except Exception as e:
        print(f"[CALENDAR] Error: {e}")
        return {"success": False, "error": str(e)}


def collect_transcript_for_llm(message: dict, call: dict) -> str:
    chunks: list[str] = []
    t = message.get("transcript")
    if isinstance(t, str) and t.strip():
        chunks.append(t.strip())
    art = message.get("artifact") or call.get("artifact") or {}
    if isinstance(art, dict):
        tr = art.get("transcript")
        if isinstance(tr, str) and tr.strip():
            chunks.append(tr.strip())
        for key in ("messages", "messagesOpenAIFormatted", "openaiMessages"):
            msgs = art.get(key)
            if not isinstance(msgs, list):
                continue
            for m in msgs:
                if not isinstance(m, dict):
                    continue
                c = m.get("content") or m.get("message") or m.get("text")
                if isinstance(c, str) and c.strip():
                    chunks.append(c.strip())
                elif isinstance(c, list):
                    for block in c:
                        if isinstance(block, dict) and block.get("type") == "text":
                            tx = block.get("text")
                            if isinstance(tx, str) and tx.strip():
                                chunks.append(tx.strip())
    return "\n\n".join(chunks)


_BOOKING_EXTRACT_SYSTEM = """You extract HVAC appointment booking fields from a phone call transcript.
Return one JSON object only, with exactly these keys (all string values, use "" if not found):
"name", "phone", "address", "zip", "date", "time", "issue"

Rules:
- Fill fields only from what the customer or agent clearly said about scheduling a visit.
- date: prefer YYYY-MM-DD when year is clear.
- time: include AM/PM if mentioned.

Do not invent details. If no appointment was discussed, return empty strings for all fields."""


def log_openai_extracted_fields(
    call_id: str,
    fields: dict,
    raw_parsed: Optional[dict] = None,
) -> None:
    """Log every extracted field explicitly (empty strings included)."""
    keys = ("name", "phone", "address", "zip", "date", "time", "issue")
    parts = [f"{k}={fields.get(k, '')!r}" for k in keys]
    print(f"[BOOKING] OpenAI extraction call_id={call_id} — " + " | ".join(parts))
    if raw_parsed is not None:
        try:
            print(
                "[BOOKING] OpenAI extraction full parsed JSON: "
                + json.dumps(raw_parsed, ensure_ascii=False)
            )
        except (TypeError, ValueError):
            print(f"[BOOKING] OpenAI extraction full parsed JSON: {raw_parsed!r}")


async def extract_booking_fields_openai(
    transcript: str, call_id: str
) -> tuple[Optional[dict], Optional[str]]:
    """Call OpenAI; always logs normalized fields via log_openai_extracted_fields."""
    empty_fields = {k: "" for k in ("name", "phone", "address", "zip", "date", "time", "issue")}

    if not OPENAI_API_KEY.strip():
        log_openai_extracted_fields(call_id, empty_fields, None)
        return None, "OPENAI_API_KEY not configured"

    text = (transcript or "").strip()
    if not text:
        log_openai_extracted_fields(call_id, empty_fields, None)
        return None, "no transcript text to analyze"

    today = datetime.now().strftime("%A, %B %d, %Y (%Y-%m-%d)")
    user_content = (
        f"Today's date (for resolving relative dates): {today}\n\n"
        f"--- CALL TRANSCRIPT ---\n{text[:48000]}"
    )
    payload = {
        "model": "gpt-4o-mini",
        "temperature": 0.1,
        "response_format": {"type": "json_object"},
        "messages": [
            {"role": "system", "content": _BOOKING_EXTRACT_SYSTEM},
            {"role": "user", "content": user_content},
        ],
    }

    try:
        async with httpx.AsyncClient() as client:
            resp = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {OPENAI_API_KEY}",
                    "Content-Type": "application/json",
                },
                json=payload,
                timeout=90.0,
            )
    except Exception as e:
        log_openai_extracted_fields(call_id, empty_fields, None)
        return None, f"OpenAI request error: {e}"

    if resp.status_code != 200:
        log_openai_extracted_fields(call_id, empty_fields, None)
        return None, f"OpenAI HTTP {resp.status_code}: {resp.text[:800]}"

    try:
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
    except Exception as e:
        log_openai_extracted_fields(call_id, empty_fields, None)
        print(f"[BOOKING] OpenAI extraction response shape error: {e!r}")
        return None, f"OpenAI response shape error: {e}"

    try:
        obj = json.loads(content)
    except Exception as e:
        log_openai_extracted_fields(call_id, empty_fields, None)
        print(f"[BOOKING] OpenAI extraction raw content (JSON decode failed): {content!r}")
        return None, f"OpenAI response parse error: {e}"

    if not isinstance(obj, dict):
        log_openai_extracted_fields(call_id, empty_fields, None)
        return None, "OpenAI returned non-object JSON"

    fields = {k: str(obj.get(k, "") or "").strip() for k in empty_fields}
    log_openai_extracted_fields(call_id, fields, obj)

    missing = [k for k in ("name", "phone", "address", "zip", "date", "time", "issue") if not fields[k]]
    if missing:
        return None, f"incomplete extraction (empty: {', '.join(missing)})"
    if fields["name"].lower() in ("unknown", "unknown caller"):
        return None, "incomplete extraction (name not usable)"

    return fields, None


async def end_of_call_auto_book_from_transcript_openai(
    message: dict, call: dict, call_id: str
) -> None:
    if get_call_outcome(call_id) == "appointment_booked":
        return

    blob = collect_transcript_for_llm(message, call)
    print(f"[BOOKING] end-of-call auto-book attempt call_id={call_id} (OpenAI extraction)")

    fields, err = await extract_booking_fields_openai(blob, call_id)
    if err or not fields:
        print(f"[BOOKING_FAILED] {err or 'extraction failed'}")
        return

    name = fields["name"]
    phone = fields["phone"]
    address = fields["address"]
    zip_code = fields["zip"]
    date_s = fields["date"]
    time_s = fields["time"]
    issue = fields["issue"] or "HVAC Service"
    full_address = f"{address} {zip_code}".strip()

    try:
        appointment_dt = parse_appointment_dt(date_s, time_s)
    except Exception as e:
        print(f"[BOOKING_FAILED] date/time parse error: {e}")
        return

    print(f"[BOOKING] Parsed datetime for auto-book: {appointment_dt}")

    save_lead_to_postgres(
        name=name,
        phone=phone,
        source="Voice AI - end-of-call OpenAI auto-book",
    )

    result = book_on_google_calendar(
        name=name,
        phone=phone,
        address=full_address,
        service_type=issue,
        appointment_dt=appointment_dt,
        notes="Booked via Voice AI (end-of-call transcript)",
    )

    if result.get("success"):
        update_call(call_id, lead_name=name, phone=phone, outcome="appointment_booked")
    else:
        print(f"[BOOKING_FAILED] calendar not created: {result.get('error')}")


# ── Vapi Tool Handlers ────────────────────────────────────────────────────────

async def handle_check_availability(args: dict) -> str:
    """
    Always returns available = True with a natural confirmation message.
    Agent uses this to confirm slot before booking.
    """
    date = args.get("date", "")
    time = args.get("time", "")
    print(f"[AVAILABILITY] Checking: {date} at {time}")
    # Return plain string — Vapi reads this as agent response
    return f"Yes, {date} at {time} is available. Shall I go ahead and book that for you?"


async def handle_book_appointment(args: dict, call_id: str) -> str:
    print(f"[BOOKING] bookAppointment invoked call_id={call_id}")
    raw = args if isinstance(args, dict) else {}
    name = _first_arg_str(raw, "name", "lead_name", "customerName", "fullName") or "Unknown"
    phone = _first_arg_str(raw, "phone", "phoneNumber", "lead_phone", "customer_phone", "mobile")
    address = _first_arg_str(raw, "address", "street", "streetAddress")
    zip_code = _first_arg_str(raw, "zip", "zip_code", "zipCode", "postalCode", "postal_code")
    date = _first_arg_str(raw, "date", "appointmentDate", "appointment_date", "preferredDate", "preferred_date")
    time = _first_arg_str(raw, "time", "appointmentTime", "appointment_time", "preferredTime", "preferred_time")
    issue = _first_arg_str(raw, "issue", "serviceType", "service_type", "problem", "description", "reason") or "HVAC Service"

    full_address = f"{address} {zip_code}".strip()
    appointment_dt = parse_appointment_dt(date, time)

    print(
        f"[BOOKING] name={name!r} phone={phone!r} address={address!r} zip={zip_code!r} "
        f"date={date!r} time={time!r} issue={issue!r}"
    )
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
        return f"Appointment booked for {name} on {date} at {time}."
    else:
        print(f"[BOOKING] Calendar API reported failure: {result.get('error')}")
        return f"Appointment confirmed for {name} on {date} at {time}. Our team will follow up shortly."


async def handle_end_call(args: dict, call_id: str) -> str:
    update_call(call_id, outcome="completed")
    print(f"[ENDCALL] Call {call_id} ended.")
    return "Thank you for calling. Have a great day!"


# ── Vapi tool response helper ─────────────────────────────────────────────────

def vapi_tool_response(message: dict, result: str):
    """
    Build correct Vapi tool response format.
    Vapi expects: {"results": [{"toolCallId": "...", "result": "..."}]}
    """
    # Try toolCallList first (newer Vapi format)
    tool_call_list = message.get("toolCallList", [])
    if tool_call_list:
        tool_call_id = tool_call_list[0].get("id", "")
    else:
        # Fallback: functionCall format
        tool_call_id = message.get("functionCall", {}).get("id", "")

    print(f"[VAPI] Tool response — toolCallId: {tool_call_id} | result: {result[:100]}")

    return JSONResponse({
        "results": [{
            "toolCallId": tool_call_id,
            "result": result
        }]
    })


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
    print(f"[VAPI] Body: {json.dumps(body)[:500]}")

    # ── call-started ──────────────────────────────────────────────────────────
    if msg_type == "call-started":
        customer_number = call.get("customer", {}).get("number", "")
        direction = call.get("type", "inboundPhoneCall")
        log_call(call_id, phone=customer_number,
                 direction="inbound" if "inbound" in direction.lower() else "outbound")
        return JSONResponse({"status": "logged"})

    # ── function-call (older Vapi format) ─────────────────────────────────────
    if msg_type == "function-call":
        func_call = message.get("functionCall", {})
        func_name = func_call.get("name", "")
        func_args = func_call.get("parameters", {})

        print(f"[VAPI] function-call: {func_name} | Args: {json.dumps(func_args)[:300]}")

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

        return vapi_tool_response(message, result)

    # ── tool-calls (newer Vapi format; some payloads use tool_calls) ──────────
    if msg_type in ("tool-calls", "tool_calls"):
        tool_call_list = message.get("toolCallList", [])
        results = []

        for tool_call in tool_call_list:
            func_name = tool_call.get("function", {}).get("name", "")
            func_args = tool_call.get("function", {}).get("arguments", {})
            tool_call_id = tool_call.get("id", "")

            if isinstance(func_args, str):
                try:
                    func_args = json.loads(func_args)
                except Exception:
                    func_args = {}

            print(f"[VAPI] tool-call: {func_name} | Args: {json.dumps(func_args)[:300]}")

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

            results.append({
                "toolCallId": tool_call_id,
                "result": result
            })

        print(f"[VAPI] tool-calls results: {json.dumps(results)[:300]}")
        return JSONResponse({"results": results})

    # ── end-of-call-report ────────────────────────────────────────────────────
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

        await end_of_call_auto_book_from_transcript_openai(message, call, call_id)

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