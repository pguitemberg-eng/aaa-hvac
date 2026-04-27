"""
agent/graph.py
LangGraph pipeline: qualify -> book -> followup -> notify
"""

from langgraph.graph import StateGraph, END
from langchain_anthropic import ChatAnthropic
from typing import TypedDict, Annotated
from langchain_core.messages import BaseMessage
import operator
from dotenv import load_dotenv
from config import get_anthropic_api_key

load_dotenv()

from nodes.qualify_node import qualify_lead
from nodes.book_node import book_lead
from nodes.followup_node import send_followup
from nodes.notify_node import notify_team


class HVACState(TypedDict):
    messages: Annotated[list[BaseMessage], operator.add]
    lead_name: str
    lead_phone: str
    lead_email: str
    lead_address: str
    lead_service_type: str
    lead_urgency: str
    lead_budget: str
    is_qualified: bool
    qualification_reason: str
    booking_url: str
    booking_confirmed: bool
    followup_count: int
    followup_max: int
    outcome: str
    source: str
    error: str


def build_graph():
    llm = ChatAnthropic(
        model="claude-sonnet-4-6",
        api_key=get_anthropic_api_key(),
        temperature=0.3,
        max_tokens=1024,
    )

    def qualify_node(state: HVACState) -> HVACState:
        return qualify_lead(state, llm)

    def book_node(state: HVACState) -> HVACState:
        return book_lead(state)

    def followup_node(state: HVACState) -> HVACState:
        return send_followup(state)

    def notify_node(state: HVACState) -> HVACState:
        return notify_team(state)

    def route_after_qualify(state: HVACState) -> str:
        if state.get("is_qualified"):
            return "book"
        return "notify"

    def route_after_book(state: HVACState) -> str:
        if state.get("booking_confirmed"):
            return "notify"
        return "followup"

    def route_after_followup(state: HVACState) -> str:
        count = state.get("followup_count", 0)
        max_count = state.get("followup_max", 3)
        outcome = state.get("outcome", "")
        if outcome == "escalated" or count >= max_count:
            return "notify"
        return "book"

    graph = StateGraph(HVACState)

    graph.add_node("qualify", qualify_node)
    graph.add_node("book", book_node)
    graph.add_node("followup", followup_node)
    graph.add_node("notify", notify_node)

    graph.set_entry_point("qualify")

    graph.add_conditional_edges("qualify", route_after_qualify, {"book": "book", "notify": "notify"})
    graph.add_conditional_edges("book", route_after_book, {"notify": "notify", "followup": "followup"})
    graph.add_conditional_edges("followup", route_after_followup, {"notify": "notify", "book": "book"})
    graph.add_edge("notify", END)

    return graph.compile()