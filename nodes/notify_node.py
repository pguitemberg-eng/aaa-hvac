"""
nodes/notify_node.py
Node 4: RAG + structured output + HubSpot MCP + Gmail MCP + Calendar MCP.
"""

import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage
from rag.rag_engine import get_rag_engine
from nodes.schemas import TechBriefing
from mcp.hubspot_mcp import update_deal_stage
from mcp.gmail_mcp import send_team_alert
from mcp.calendar_mcp import create_job_event
from tools.twilio_tool import send_sms

TECH_BRIEFING_SYSTEM_PROMPT = """
You are an HVAC dispatcher creating a technical briefing for a field technician.
Use the HVAC knowledge provided to fill in all fields with specific, accurate information.
The technician reads this before arriving - be precise about parts and tools.
""".strip()


def notify_team(state: dict) -> dict:
    outcome = state.get("outcome", "")
    name = state.get("lead_name", "Unknown")
    phone = state.get("lead_phone", "N/A")
    email = state.get("lead_email", "N/A")
    service = state.get("lead_service_type", "HVAC")
    urgency = state.get("lead_urgency", "routine")
    address = state.get("lead_address", "N/A")
    reason = state.get("qualification_reason", "")
    booking = state.get("booking_url", "N/A")
    count = state.get("followup_count", 0)

    print(f"[NOTIFY] outcome={outcome} | lead={name}")

    rag_engine = get_rag_engine()
    docs = rag_engine.retrieve(f"{service} {urgency} diagnosis parts price", n_results=3)
    hvac_context = rag_engine.format_context(docs)
    print(f"[NOTIFY] RAG retrieved {len(docs)} docs")

    briefing: TechBriefing | None = None

    if state.get("booking_confirmed") or outcome == "escalated":
        llm = ChatAnthropic(
            model="claude-sonnet-4-6",
            api_key=os.getenv("ANTHROPIC_API_KEY"),
            temperature=0.2,
            max_tokens=400,
        )
        structured_llm = llm.with_structured_output(TechBriefing)
        try:
            briefing = structured_llm.invoke([
                SystemMessage(content=TECH_BRIEFING_SYSTEM_PROMPT),
                HumanMessage(content=f"""
HVAC KNOWLEDGE (RAG):
{hvac_context}

Lead: {name} | Service: {service} | Urgency: {urgency}
Address: {address} | Diagnosis: {reason}
""".strip()),
            ])
            print(f"[NOTIFY] TechBriefing: {len(briefing.likely_diagnoses)} diagnoses, {len(briefing.parts_to_bring)} parts")
        except Exception as e:
            print(f"[NOTIFY] Briefing fallback ({e})")

    briefing_text = ""
    if briefing:
        briefing_text = (
            f"Likely diagnoses: {', '.join(briefing.likely_diagnoses)}\n"
            f"Parts to bring: {', '.join(briefing.parts_to_bring)}\n"
            f"Price estimate: {briefing.price_range}\n"
            f"Priority: {briefing.priority_level.upper()}"
        )
        if briefing.special_notes:
            briefing_text += f"\nNotes: {briefing.special_notes}"

    if state.get("booking_confirmed"):
        final_outcome = "booked"
        sms_msg = f"BOOKING: {name} | {service} | {urgency.upper()} | {phone} | {address}"
    elif outcome == "escalated":
        final_outcome = "escalated"
        sms_msg = f"ESCALATE: {name} | {service} | {count} ignored | {phone} | CALL NOW"
    else:
        final_outcome = "disqualified"
        sms_msg = f"Disqualified: {name} | {reason[:70]}"

    update_deal_stage({**state, "outcome": final_outcome})

    biz_email = os.getenv("BUSINESS_EMAIL", "")
    if biz_email:
        send_team_alert(
            state={**state, "outcome": final_outcome},
            outcome=final_outcome,
            tech_briefing_text=briefing_text,
        )

    if final_outcome == "booked":
        create_job_event(state)

    biz_phone = os.getenv("BUSINESS_PHONE", "")
    if biz_phone:
        send_sms(to=biz_phone, body=sms_msg)
        print(f"[NOTIFY] SMS sent to team")

    print(f"[NOTIFY] Complete | final outcome: {final_outcome}")
    return {**state, "outcome": final_outcome}