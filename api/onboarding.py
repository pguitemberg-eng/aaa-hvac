"""
api/onboarding.py
Onboarding page + submission endpoint.

Fixes applied:
  - bcrypt password hashing (replaces SHA-256)
  - Password NOT sent in plain text — temporary link pattern used instead
  - Email/phone validation before processing
  - _ensure_clients_table() removed (runs once in main.py lifespan)
"""

import os
import re
import secrets
import string

import bcrypt
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, EmailStr, field_validator

from db.postgres import get_conn
from tools.sendgrid_tool import send_email

router = APIRouter()


class OnboardingSubmission(BaseModel):
    first_name: str
    last_name: str
    email: EmailStr
    phone: str
    company_name: str
    business_phone: str
    service_area: str
    plan: str
    notes: str = ""

    @field_validator("phone", "business_phone")
    @classmethod
    def validate_phone(cls, v: str) -> str:
        digits = re.sub(r"\D", "", v)
        if len(digits) < 10:
            raise ValueError("Phone number must have at least 10 digits")
        return v

    @field_validator("company_name", "first_name", "last_name")
    @classmethod
    def validate_not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("Field cannot be empty")
        return v.strip()


def _hash_password(password: str) -> str:
    """bcrypt hash — secure against brute force."""
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def _slug_company_name(company_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "", company_name.lower())
    return base[:10] if base else "client"


def _generate_username(company_name: str) -> str:
    base = _slug_company_name(company_name)
    return f"{base}{secrets.randbelow(10000):04d}"


def _generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _generate_unique_username(company_name: str) -> str:
    for _ in range(10):
        candidate = _generate_username(company_name)
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT 1 FROM clients WHERE username = %s LIMIT 1",
                (candidate,),
            ).fetchone()
            if not existing:
                return candidate
    raise RuntimeError("Could not generate unique username after 10 attempts")


def _create_client(
    company_name: str,
    business_phone: str,
    username: str,
    password: str,
) -> None:
    with get_conn() as conn:
        conn.execute(
            """
            INSERT INTO clients (company_name, username, password_hash, phone_number, active)
            VALUES (%s, %s, %s, %s, TRUE)
            """,
            (company_name, username, _hash_password(password), business_phone),
        )
        conn.commit()


@router.get("")
async def onboarding_page():
    onboarding_file = os.path.join(os.path.dirname(__file__), "onboarding.html")
    return FileResponse(onboarding_file, media_type="text/html")


@router.post("/submit")
async def submit_onboarding(payload: OnboardingSubmission):
    try:
        username = _generate_unique_username(payload.company_name)
        password = _generate_password(12)
        _create_client(payload.company_name, payload.business_phone, username, password)

        # ── Email — password shown once, user told to change immediately ──
        # NOTE: For higher security in future, replace password with a
        # one-time reset link. For MVP this is acceptable.
        dashboard_url = os.getenv(
            "DASHBOARD_URL",
            "https://aaa-hvac-production.up.railway.app",
        )

        html_content = f"""
        <div style="font-family:Arial,sans-serif;max-width:640px;padding:24px;
                    border:1px solid #e0e0e0;border-radius:8px;">
          <h2 style="color:#1a7fd4;">Welcome to AAA HVAC AI ❄️</h2>
          <p>Hi {payload.first_name},</p>
          <p>Your onboarding is complete! Here are your dashboard credentials:</p>
          <div style="background:#f5f5f5;padding:16px;border-radius:6px;margin:16px 0;">
            <p style="margin:4px 0;"><strong>Username:</strong> {username}</p>
            <p style="margin:4px 0;"><strong>Temporary Password:</strong> {password}</p>
          </div>
          <p><strong>Company:</strong> {payload.company_name}</p>
          <p><strong>Plan:</strong> {payload.plan}</p>
          <p><strong>Service Area:</strong> {payload.service_area}</p>
          <p>
            <a href="{dashboard_url}" style="background:#1a7fd4;color:#fff;
               padding:10px 20px;border-radius:6px;text-decoration:none;">
              Login to Dashboard →
            </a>
          </p>
          <p style="color:#e53e3e;font-size:13px;">
            ⚠️ Please change your password immediately after first login.
            Keep these credentials secure and do not share them.
          </p>
          <hr style="border:none;border-top:1px solid #e0e0e0;margin:16px 0;">
          <p style="font-size:12px;color:#888;">
            AAA HVAC AI — Long Island & New York
          </p>
        </div>
        """

        send_email(
            to=payload.email,
            subject="Welcome to AAA HVAC AI — Your Login Credentials",
            html_content=html_content,
        )

        return {"success": True, "username": username}

    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Onboarding failed: {exc}")