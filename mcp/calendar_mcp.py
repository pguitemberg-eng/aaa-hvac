"""
mcp/calendar_mcp.py
Technician availability and job scheduling via Google Calendar MCP.
"""

from mcp.mcp_client import call_mcp

CALENDAR_SYSTEM = (
    "You are an HVAC scheduling assistant managing technician calendars. "
    "Check availability accurately and confirm what slots are open."
)


def check_technician_availability(urgency: str = "routine") -> dict:
    time_window = "next 4 hours" if urgency == "emergency" else "next 48 hours"

    print(f"[CALENDAR] Checking technician availability ({urgency})")

    prompt = f"""
Check Google Calendar for all calendars named "HVAC Technician" or similar.
Find the first available slot in the {time_window} for any technician.

Return:
1. Is any technician available? (yes/no)
2. The earliest available slot (date, time range)
3. Which technician is available
4. Any conflicts or notes

Be specific about times. Use Eastern Time (ET).
""".strip()

    result = call_mcp(prompt=prompt, server_key="gcal", system=CALENDAR_SYSTEM)

    if not result:
        return {"available": True, "next_slot": None, "technician": None, "raw_response": ""}

    result_lower = result.lower()
    available = not any(phrase in result_lower for phrase in [
        "no availability", "not available", "fully booked", "no open slots"
    ])

    return {
        "available": available,
        "next_slot": result,
        "technician": None,
        "raw_response": result,
    }


def create_job_event(state: dict, confirmed_time: str = "") -> bool:
    name = state.get("lead_name", "")
    phone = state.get("lead_phone", "")
    address = state.get("lead_address", "")
    service = state.get("lead_service_type", "")
    urgency = state.get("lead_urgency", "routine")

    print(f"[CALENDAR] Creating job event for {name}")

    time_info = f"Scheduled time: {confirmed_time}" if confirmed_time else "Time: TBD - assign to next available technician"

    prompt = f"""
Create a Google Calendar event for an HVAC service job:

  Title: HVAC Job - {name} - {service}
  {time_info}
  Duration: 2 hours (estimated)
  Location: {address}
  Description: Customer: {name} | Phone: {phone} | Service: {service} | Urgency: {urgency.upper()}
  Calendar: Add to the first available technician's calendar
  Reminders: 1 hour before (email + popup)
  Color: {"Red (urgent)" if urgency in ("emergency", "urgent") else "Blue (routine)"}

Confirm the event was created and which technician's calendar it was added to.
""".strip()

    result = call_mcp(prompt=prompt, server_key="gcal", system=CALENDAR_SYSTEM)
    return result is not None