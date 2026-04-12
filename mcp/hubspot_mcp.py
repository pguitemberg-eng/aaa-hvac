"""
mcp/hubspot_mcp.py
CRM operations via HubSpot MCP server.
"""

from mcp.mcp_client import call_mcp

HUBSPOT_SYSTEM = (
    "You are an HVAC CRM assistant managing HubSpot for a Long Island HVAC company. "
    "Complete the requested CRM operation precisely and confirm what was done."
)


def create_or_update_contact(state: dict) -> bool:
    name = state.get("lead_name", "")
    phone = state.get("lead_phone", "")
    email = state.get("lead_email", "")
    address = state.get("lead_address", "")
    service = state.get("lead_service_type", "")
    urgency = state.get("lead_urgency", "routine")

    print(f"[HUBSPOT] Creating/updating contact: {name}")

    prompt = f"""
In HubSpot, create or update a contact and associated deal with these details:

CONTACT:
  Name: {name}
  Phone: {phone}
  Email: {email}
  Address: {address}

DEAL:
  Name: {name} - {service}
  Pipeline: Sales Pipeline
  Stage: appointmentscheduled
  Priority: {urgency.upper()}
  Description: HVAC lead auto-created by AI agent. Service: {service}

If a contact with this phone or email already exists, update it.
Then confirm: contact ID and deal ID created or updated.
""".strip()

    result = call_mcp(prompt=prompt, server_key="hubspot", system=HUBSPOT_SYSTEM)
    return result is not None


def update_deal_stage(state: dict) -> bool:
    name = state.get("lead_name", "")
    phone = state.get("lead_phone", "")
    outcome = state.get("outcome", "")

    stage_map = {
        "booked": "qualifiedtobuy",
        "escalated": "appointmentscheduled",
        "disqualified": "closedlost",
    }
    stage = stage_map.get(outcome, "appointmentscheduled")

    print(f"[HUBSPOT] Updating deal stage: {name} -> {stage}")

    prompt = f"""
In HubSpot, find the open deal for contact with phone {phone} (name: {name}).
Update the deal stage to: {stage}
Add a note: "Agent outcome: {outcome}. Updated automatically by AAA HVAC AI agent."
Confirm the deal ID and new stage.
""".strip()

    result = call_mcp(prompt=prompt, server_key="hubspot", system=HUBSPOT_SYSTEM)
    return result is not None


def log_activity(state: dict, followup_num: int, message: str) -> bool:
    name = state.get("lead_name", "")
    phone = state.get("lead_phone", "")

    print(f"[HUBSPOT] Logging follow-up #{followup_num} for {name}")

    prompt = f"""
In HubSpot, find the contact with phone {phone} (name: {name}).
Log a call/note activity on their timeline:
  Type: Note
  Subject: Automated Follow-up #{followup_num}
  Body: {message}
  Date: Today
Confirm the activity was logged.
""".strip()

    result = call_mcp(prompt=prompt, server_key="hubspot", system=HUBSPOT_SYSTEM)
    return result is not None