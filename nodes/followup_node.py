"""
nodes/followup_node.py
Node 3: RAG + Claude structured output + HubSpot MCP + Gmail MCP.
"""

import os
from langchain_anthropic import ChatAnthropic
from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from rag.rag_engine import get_rag_engine
from nodes.schemas import FollowupMessage
from memory.customer_db import log_followup
from mcp.hubspot_mcp import log_activity
from mcp.gmail_mcp import send_followup_email
from tools.twilio_tool import send_sms
from tools.sendgrid_tool import send_email as sendgrid_email
from config import get_anthropic_api_key

FOLLOWUP_SYSTEM_PROMPT = """
You are an HVAC service coordinator writing a follow-up SMS.
Rules:
- Maximum 2 sentences
- Reference the specific HVAC problem from the RAG context
- Match the tone to the follow-up number provided in the prompt
- Use {booking_url} as the booking link placeholder
- Use {business_phone} as the business phone placeholder
- English only
""".strip()

TONE_GUIDE = {
    1: "gentle - they may have been busy, friendly reminder",
    2: "more urgent - the HVAC problem could get worse if ignored",
    3: "last chance - offer to call them directly instead of booking online",
}


def send_followup(state: dict) -> dict:
    count = state.get("followup_count", 0)
    max_count = state.get("followup_max", 3)

    print(f"[FOLLOWUP] Sending #{count + 1} of {max_count}")

    if count >= max_count:
        print(f"[FOLLOWUP] Limit reached - escalating")
        return {**state, "outcome": "escalated"}

    service = state.get("lead_service_type", "HVAC")
    urgency = state.get("lead_urgency", "routine")
    rag_engine = get_rag_engine()
    docs = rag_engine.retrieve(f"{service} {urgency} follow-up", n_results=2)
    hvac_context = rag_engine.format_context(docs)
    print(f"[FOLLOWUP] RAG retrieved {len(docs)} docs")

    followup_num = count + 1
    tone = TONE_GUIDE.get(followup_num, "gentle reminder")

    llm = ChatAnthropic(
        model="claude-sonnet-4-6",
        api_key=get_anthropic_api_key(),
        temperature=0.5,
        max_tokens=200,
    )
    structured_llm = llm.with_structured_output(FollowupMessage)

    try:
        result: FollowupMessage = structured_llm.invoke([
            SystemMessage(content=FOLLOWUP_SYSTEM_PROMPT),
            HumanMessage(content=f"""
HVAC KNOWLEDGE (RAG):
{hvac_context}

Lead: {state.get('lead_name', '')} | Problem: {service} ({urgency})
Follow-up number: {followup_num} | Tone: {tone}
""".strip()),
        ])
        sms_body = (
            result.sms_body
            .replace("{booking_url}", state.get("booking_url", ""))
            .replace("{business_phone}", os.getenv("BUSINESS_PHONE", ""))
        )
        tone_used = result.tone_used
        print(f"[FOLLOWUP] Message ({tone_used}): {sms_body[:80]}...")
    except Exception as e:
        sms_body = (
            f"Hi {state.get('lead_name', '')}! Still need help with your {service}? "
            f"Book: {state.get('booking_url', '')} or call {os.getenv('BUSINESS_PHONE', '')}"
        )
        tone_used = "gentle"
        print(f"[FOLLOWUP] Fallback used ({e})")

    if state.get("lead_phone"):
        send_sms(to=state["lead_phone"], body=sms_body)
        print(f"[FOLLOWUP] SMS sent")

    if state.get("lead_email"):
        gmail_ok = send_followup_email(
            state=state,
            followup_num=followup_num,
            sms_body=sms_body,
            hvac_context=hvac_context,
        )
        if not gmail_ok:
            sendgrid_email(
                to=state["lead_email"],
                subject=f"Following up - {state.get('lead_name', '')}",
                html_content=f"<p>{sms_body}</p>",
            )

    log_activity(
        state=state,
        followup_num=followup_num,
        message=f"[{tone_used.upper()}] {sms_body}",
    )

    log_followup(
        state=state,
        followup_num=followup_num,
        message=sms_body,
        tone_used=tone_used,
        channel="sms+email",
    )

    return {
        **state,
        "followup_count": count + 1,
        "messages": state["messages"] + [
            AIMessage(content=f"[FOLLOWUP #{followup_num}] [{tone_used}] {sms_body}")
        ],
    }