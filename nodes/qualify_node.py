"""
nodes/qualify_node.py
Node 1: RAG + Memory + Claude structured output + HubSpot MCP.
"""

from langchain_core.messages import SystemMessage, HumanMessage, AIMessage
from rag.rag_engine import get_rag_engine
from memory.customer_db import (
    get_customer_history, format_customer_context,
    save_lead, upsert_customer,
)
from nodes.schemas import QualifyResult
from mcp.hubspot_mcp import create_or_update_contact

QUALIFY_SYSTEM_PROMPT = """
You are an expert HVAC AI dispatcher for a service company in Long Island, New York.

YOUR JOB:
- Analyze the HVAC problem described by the lead
- Use the HVAC KNOWLEDGE provided for a preliminary technical diagnosis
- Consider the CUSTOMER HISTORY if this is a returning customer
- Determine if the lead qualifies for our service
- Extract all key contact and service information

QUALIFICATION CRITERIA:
QUALIFIES if: real HVAC problem + in service area (Long Island/NYC) + valid contact info + needs service within 30 days
DOES NOT QUALIFY if: no HVAC need, outside service area, missing contact info, just price shopping

RETURNING CUSTOMERS: Always qualify returning customers with a positive history.
""".strip()


def qualify_lead(state: dict, llm) -> dict:
    phone = state.get("lead_phone", "")

    customer_history = get_customer_history(phone) if phone else None
    customer_context = format_customer_context(customer_history)
    if customer_history:
        print(f"[QUALIFY] Returning customer: {customer_history['name']} | {customer_history['total_jobs']} previous jobs")
    else:
        print(f"[QUALIFY] New customer")

    rag_query = _build_rag_query(state)
    rag_engine = get_rag_engine()
    docs = rag_engine.retrieve(rag_query, n_results=3)
    hvac_context = rag_engine.format_context(docs)
    print(f"[QUALIFY] RAG retrieved {len(docs)} docs for: '{rag_query}'")

    structured_llm = llm.with_structured_output(QualifyResult)

    user_content = f"""
HVAC KNOWLEDGE (via RAG):
{hvac_context}

CUSTOMER HISTORY (via Memory):
{customer_context}

LEAD DATA:
Name: {state.get('lead_name', 'Unknown')}
Phone: {state.get('lead_phone', '')}
Email: {state.get('lead_email', '')}
Address: {state.get('lead_address', '')}
Service type: {state.get('lead_service_type', '')}
Urgency: {state.get('lead_urgency', '')}
Budget: {state.get('lead_budget', '')}
Message: {_get_last_human_message(state)}

Qualify this lead.
""".strip()

    try:
        result: QualifyResult = structured_llm.invoke([
            SystemMessage(content=QUALIFY_SYSTEM_PROMPT),
            HumanMessage(content=user_content),
        ])

        print(f"[QUALIFY] qualified={result.is_qualified} | urgency={result.lead_urgency} | type={result.lead_service_type}")

        updated_state = {
            **state,
            "is_qualified": result.is_qualified,
            "qualification_reason": result.qualification_reason,
            "lead_name": result.lead_name or state.get("lead_name", ""),
            "lead_phone": result.lead_phone or state.get("lead_phone", ""),
            "lead_email": result.lead_email or state.get("lead_email", ""),
            "lead_address": result.lead_address or state.get("lead_address", ""),
            "lead_service_type": result.lead_service_type or state.get("lead_service_type", ""),
            "lead_urgency": result.lead_urgency or state.get("lead_urgency", "routine"),
            "lead_budget": result.lead_budget or state.get("lead_budget", ""),
            "messages": state["messages"] + [
                AIMessage(content=f"[QUALIFY] {result.qualification_reason} | Diagnosis: {result.hvac_diagnosis}")
            ],
        }

        save_lead(updated_state)
        upsert_customer(updated_state)

        if result.is_qualified:
            create_or_update_contact(updated_state)

        return updated_state

    except Exception as e:
        print(f"[QUALIFY] Error: {e}")
        return {
            **state,
            "is_qualified": False,
            "qualification_reason": f"System error: {e}",
            "error": str(e),
        }


def _build_rag_query(state: dict) -> str:
    parts = []
    if state.get("lead_service_type"):
        parts.append(state["lead_service_type"])
    msg = _get_last_human_message(state)
    if msg:
        parts.append(msg[:150])
    return " ".join(parts) if parts else "HVAC repair Long Island"


def _get_last_human_message(state: dict) -> str:
    for msg in reversed(state.get("messages", [])):
        if isinstance(msg, HumanMessage):
            return str(msg.content)
    return ""