"""
nodes/schemas.py
Pydantic models for all structured outputs across the 4 nodes.
"""

from pydantic import BaseModel, Field
from typing import Optional


class QualifyResult(BaseModel):
    is_qualified: bool = Field(description="True if lead qualifies for service")
    qualification_reason: str = Field(description="Why qualified or not")
    hvac_diagnosis: str = Field(description="Preliminary technical diagnosis")
    lead_name: Optional[str] = Field(default=None)
    lead_phone: Optional[str] = Field(default=None)
    lead_email: Optional[str] = Field(default=None)
    lead_address: Optional[str] = Field(default=None)
    lead_service_type: Optional[str] = Field(default=None)
    lead_urgency: Optional[str] = Field(default="routine")
    lead_budget: Optional[str] = Field(default=None)


class BookMessage(BaseModel):
    sms_body: str = Field(description="SMS body with {booking_url} placeholder")
    email_subject: str = Field(description="Email subject line")
    email_opening_paragraph: str = Field(description="Opening paragraph for booking email, plain HTML")


class FollowupMessage(BaseModel):
    sms_body: str = Field(description="Follow-up SMS with {booking_url} and {business_phone} placeholders")
    tone_used: str = Field(description="Tone used: gentle, concerned, or final")


class TechBriefing(BaseModel):
    likely_diagnoses: list[str] = Field(description="Top 2-3 likely causes based on symptoms")
    parts_to_bring: list[str] = Field(description="Parts and tools the technician should have")
    price_range: str = Field(description="Estimated price range for this type of job")
    priority_level: str = Field(description="low, medium, high, or emergency")
    special_notes: str = Field(default="", description="Any special instructions for the technician")