"""
mcp/gmail_mcp.py
Send professional emails directly from the business Gmail account via MCP.
"""

import os
from mcp.mcp_client import call_mcp

GMAIL_SYSTEM = (
    "You are an HVAC business owner sending professional emails to customers. "
    "Write and send emails that feel personal, expert, and trustworthy. "
    "Always sign off with the business owner's name and company details."
)


def send_booking_email(state: dict, booking_url: str, hvac_context: str = "") -> bool:
    name = state.get("lead_name", "")
    email = state.get("lead_email", "")
    service = state.get("lead_service_type", "")
    urgency = state.get("lead_urgency", "routine")

    if not email:
        print(f"[GMAIL] No email for {name} - skipping")
        return False

    print(f"[GMAIL] Sending booking email to {name} ({email})")

    urgency_note = {
        "emergency": "I understand this is an emergency - we are prioritizing your request.",
        "urgent": "We understand your urgency and will get to you as quickly as possible.",
        "routine": "We look forward to helping you with your HVAC needs.",
    }.get(urgency, "")

    prompt = f"""
Send a professional email via Gmail with these details:

TO: {email}
SUBJECT: Your HVAC Appointment - {name}

Write the email body using this context:
- Customer name: {name}
- Service needed: {service}
- Urgency: {urgency} - {urgency_note}
- HVAC technical context: {hvac_context[:300] if hvac_context else 'Standard HVAC service'}
- Booking link: {booking_url}
- Business name: {os.getenv('BUSINESS_NAME', 'HVAC Pro')}
- Business phone: {os.getenv('BUSINESS_PHONE', '')}

The email should:
1. Acknowledge their specific HVAC problem with technical knowledge
2. Explain what our technician will likely check or fix
3. Include the booking link as a clear call-to-action
4. Sign off personally with the business owner's name

Send the email and confirm it was sent.
""".strip()

    result = call_mcp(prompt=prompt, server_key="gmail", system=GMAIL_SYSTEM)
    return result is not None


def send_followup_email(state: dict, followup_num: int,
                        sms_body: str, hvac_context: str = "") -> bool:
    name = state.get("lead_name", "")
    email = state.get("lead_email", "")
    service = state.get("lead_service_type", "")
    booking = state.get("booking_url", "")

    if not email:
        return False

    print(f"[GMAIL] Sending follow-up #{followup_num} email to {name}")

    tone_guide = {
        1: "friendly and helpful - they may have been busy",
        2: "slightly more concerned - the problem may be getting worse",
        3: "final outreach - offer to call them directly",
    }.get(followup_num, "friendly")

    prompt = f"""
Send a follow-up email via Gmail:

TO: {email}
SUBJECT: Following up - Your HVAC Request ({name})

Context:
- Customer: {name}
- Problem: {service}
- Follow-up number: {followup_num} of 3
- Tone: {tone_guide}
- HVAC knowledge: {hvac_context[:200] if hvac_context else 'Standard HVAC service'}
- Booking link: {booking}
- Business: {os.getenv('BUSINESS_NAME', 'HVAC Pro')} | {os.getenv('BUSINESS_PHONE', '')}

Write a short personal email (3-4 sentences) that matches the tone above.
Reference the specific HVAC problem. End with the booking link.
{"For follow-up 3: offer to call them directly instead of booking online." if followup_num == 3 else ""}

Send the email and confirm it was delivered.
""".strip()

    result = call_mcp(prompt=prompt, server_key="gmail", system=GMAIL_SYSTEM)
    return result is not None


def send_team_alert(state: dict, outcome: str, tech_briefing_text: str = "") -> bool:
    biz_email = os.getenv("BUSINESS_EMAIL", "")
    if not biz_email:
        return False

    name = state.get("lead_name", "")
    phone = state.get("lead_phone", "")
    address = state.get("lead_address", "")
    service = state.get("lead_service_type", "")
    urgency = state.get("lead_urgency", "")

    print(f"[GMAIL] Sending team alert for {name} ({outcome})")

    subject_map = {
        "booked": f"NEW BOOKING - {name} | {service}",
        "escalated": f"ACTION REQUIRED - {name} | Manual call needed",
        "disqualified": f"Lead closed - {name}",
    }

    prompt = f"""
Send an internal business email via Gmail:

TO: {biz_email}
SUBJECT: {subject_map.get(outcome, f'Lead update - {name}')}

Write a professional internal alert with these details:
- Customer: {name}
- Phone: {phone}
- Address: {address}
- Service: {service}
- Urgency: {urgency.upper()}
- Outcome: {outcome.upper()}

{"ACTION REQUIRED: This lead did not respond to 3 automated follow-ups. Please call manually." if outcome == "escalated" else ""}

Tech Briefing for technician:
{tech_briefing_text if tech_briefing_text else "Standard diagnostic - see lead details."}

Send the email and confirm delivery.
""".strip()

    result = call_mcp(prompt=prompt, server_key="gmail", system=GMAIL_SYSTEM)
    return result is not None