"""
api/onboarding.py
Onboarding page + submission endpoint.
"""

import hashlib
import os
import re
import secrets
import string

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel

from db.postgres import get_conn
from tools.sendgrid_tool import send_email

router = APIRouter()


class OnboardingSubmission(BaseModel):
    first_name: str
    last_name: str
    email: str
    phone: str
    company_name: str
    business_phone: str
    service_area: str
    plan: str
    notes: str = ""


def _hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def _slug_company_name(company_name: str) -> str:
    base = re.sub(r"[^a-z0-9]+", "", company_name.lower())
    return base[:10] if base else "client"


def _generate_username(company_name: str) -> str:
    base = _slug_company_name(company_name)
    return f"{base}{secrets.randbelow(10000):04d}"


def _generate_password(length: int = 12) -> str:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _ensure_clients_table() -> None:
    with get_conn() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS clients (
                id SERIAL PRIMARY KEY,
                company_name TEXT NOT NULL,
                username TEXT UNIQUE NOT NULL,
                password_hash TEXT NOT NULL,
                phone_number TEXT,
                active BOOLEAN NOT NULL DEFAULT TRUE
            )
            """
        )
        conn.commit()


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
    raise RuntimeError("Could not generate unique username")


def _create_client(company_name: str, business_phone: str, username: str, password: str) -> None:
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
        _ensure_clients_table()
        username = _generate_unique_username(payload.company_name)
        password = _generate_password(12)
        _create_client(payload.company_name, payload.business_phone, username, password)

        html_content = f"""
        <div style="font-family:Arial,sans-serif;max-width:640px;">
          <h2>Welcome to AAA HVAC AI</h2>
          <p>Hi {payload.first_name},</p>
          <p>Your onboarding is complete. Your dashboard credentials are below:</p>
          <ul>
            <li><strong>Username:</strong> {username}</li>
            <li><strong>Password:</strong> {password}</li>
          </ul>
          <p><strong>Company:</strong> {payload.company_name}</p>
          <p><strong>Plan:</strong> {payload.plan}</p>
          <p><strong>Service Area:</strong> {payload.service_area}</p>
          <p>Keep these credentials secure and change your password after first login.</p>
        </div>
        """
        send_email(
            to=payload.email,
            subject="Welcome to AAA HVAC AI - Your Login Credentials",
            html_content=html_content,
        )
        return {"success": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Onboarding failed: {exc}")
