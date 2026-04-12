"""
nodes/book_node.py
Node 2: RAG + Calendar MCP + Gmail MCP + Calendly + structured output.
"""

import os
import requests
from tenacity import retry, stop_after_attempt, wait_exponential
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from rag.rag_engine import get_rag_engine
from nodes.schemas import BookMessage
from mcp.calendar_mcp import check_technician_availability
from mcp.gmail_mcp import send_booking_email
from tools.twilio_tool import send_sms
from tools.sendgrid_tool import send_email as sendgrid_email

BOOK_SYSTEM_PROMPT = """
You are an HVAC expert writing booking messages for a customer in Long Island, NY.
Based on the HVAC knowledge provided, write messages that:
- Reference the specific technical issue, not generic copy
- Show expertise about the likely cause
- Create appropriate urgency based on the problem type
- Are professional, warm, and concise

For the SMS: use {booking_url} as the booking link placeholder.
For the email paragraph: return plain HTML, no html or body tags.
""".strip()


@retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=2, max=10))
def _create_calendly_link() -> str:
    response = requests.post(
        "https://api.calendly.com/scheduling_links",
        json={
            "max_event_count": 1,
            "owner": os.getenv("CALENDLY_EVENT_TYPE_URI"),
            "owner_type": "EventType",
        },
        headers={
            "Authorization": f"Bearer {os.getenv('CALENDLY_API_KEY')}",
            "Content-Type": "application/json",
        },
        timeout=10,
    )
    response.raise_for_status()
    return response.json()["resource"]["booking_url"]


def book_lead(state: dict) -> dict:
    name = state.get("lead_name", "")
    phone = state.get("lead_phone", "")
    email = state.get("lead_email", "")
    service = state.get("lead_service_type", "HVAC service")
    urgency = state.get("lead_urgency", "routine")
    reason = state.get("qualification_reason", "")

    print(f"[BOOK] Starting for {name}...")

    rag_engine = get_rag_engine()
    docs = rag_engine.retrieve(f"{service} {urgency} Long Island", n_results=3)
    hvac_context = rag_engine.format_context(docs)
    print(f"[BOOK] RAG retrieved {len(docs)} docs")

    availability = check_technician_availability(urgency)
    if availability["available"]:
        print(f"[BOOK] Calendar: technician available")
    else:
        print(f"[BOOK] Calendar: no availability - flagging for manual follow-up")

    try:
        booking_url = _create_calendly_link()
        print(f"[BOOK] Calendly link created")
    except Exception as e:
        print(f"[BOOK] Calendly failed: {e}")
        return {**state, "booking_url": "", "booking_confirmed": False, "error": str(e)}

    llm = ChatAnthropic(
        model="claude-sonnet-4-6",
        api_key=os.getenv("ANTHROPIC_API_KEY"),
        temperature=0.4,
        max_tokens=600,
    )
    structured_llm = llm.with_structured_output(BookMessage)
    urgency_label = {"emergency": "URGENT", "urgent": "URGENT", "routine": "Appointment"}.get(urgency, "Appointment")

    availability_note = (
        f"Good news: a technician is available {availability.get('next_slot', 'soon')}."
        if availability["available"]
        else "We will confirm a technician shortly after booking."
    )

    try:
        result: BookMessage = structured_llm.invoke([
            SystemMessage(content=BOOK_SYSTEM_PROMPT),
            HumanMessage(content=f"""
HVAC KNOWLEDGE (RAG):
{hvac_context}

Lead: {name} | Service: {service} | Urgency: {urgency_label}
Preliminary diagnosis: {reason}
Availability: {availability_note}
Business: {os.getenv('BUSINESS_NAME', 'HVAC Pro')} | {os.getenv('BUSINESS_PHONE', '')}
""".strip()),
        ])

        sms_body = result.sms_body.replace("{booking_url}", booking_url)
        email_subject = result.email_subject
        expert_para = result.email_opening_paragraph
        print(f"[BOOK] BookMessage generated")

    except Exception as e:
        sms_body = (
            f"Hi {name}! We received your {service} request. "
            f"Book here: {booking_url} | Call: {os.getenv('BUSINESS_PHONE', '')}"
        )
        email_subject = f"Book Your HVAC Appointment - {name}"
        expert_para = f"<p>We've received your <strong>{service}</strong> request.</p>"
        print(f"[BOOK] Fallback messages used ({e})")

    if phone:
        ok = send_sms(to=phone, body=sms_body)
        print(f"[BOOK] SMS: {'sent' if ok else 'failed'}")

    if email:
        gmail_ok = send_booking_email(
            state=state,
            booking_url=booking_url,
            hvac_context=hvac_context,
        )
        if not gmail_ok:
            print(f"[BOOK] Gmail MCP failed - falling back to SendGrid")
            email_html = _build_email_html(
                name, expert_para, booking_url,
                os.getenv("BUSINESS_NAME", "HVAC Pro"),
                os.getenv("BUSINESS_PHONE", ""),
            )
            sendgrid_email(to=email, subject=email_subject, html_content=email_html)

    return {
        **state,
        "booking_url": booking_url,
        "booking_confirmed": False,
        "outcome": "booking_link_sent",
    }


def _build_email_html(name: str, expert_para: str,
                      booking_url: str, biz_name: str, biz_phone: str) -> str:
    return f"""
<div style="font-family:Arial,sans-serif;max-width:600px;margin:0 auto;">
  <div style="background:#1a56db;padding:20px;border-radius:8px 8px 0 0;">
    <h2 style="color:white;margin:0;">{biz_name} - Book Your Appointment</h2>
  </div>
  <div style="border:1px solid #e5e7eb;padding:24px;border-radius:0 0 8px 8px;">
    <p>Hi <strong>{name}</strong>,</p>
    {expert_para}
    <a href="{booking_url}"
       style="background:#1a56db;color:white;padding:14px 28px;
              text-decoration:none;border-radius:6px;display:inline-block;
              font-size:16px;font-weight:bold;margin:12px 0;">
      Choose Appointment Time
    </a>
    <p style="color:#6b7280;font-size:14px;margin-top:16px;">
      Questions? Call: {biz_phone}<br><strong>{biz_name}</strong>
    </p>
  </div>
</div>
"""