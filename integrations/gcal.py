"""
integrations/calendar.py
"""
import os
from datetime import datetime, timedelta
from typing import Optional
from zoneinfo import ZoneInfo
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

CREDENTIALS_FILE = os.getenv("GOOGLE_CREDENTIALS_FILE", "google_credentials.json")
CALENDAR_ID = os.getenv("GOOGLE_CALENDAR_ID", "primary")
TIMEZONE = os.getenv("CALENDAR_TIMEZONE", "America/New_York")
SCOPES = ["https://www.googleapis.com/auth/calendar"]

def get_calendar_service():
    creds = service_account.Credentials.from_service_account_file(CREDENTIALS_FILE, scopes=SCOPES)
    return build("calendar", "v3", credentials=creds)

def book_appointment(customer_name, customer_phone, service_type, appointment_dt, client_id=1, lead_id=None, duration_minutes=60, notes="", customer_email=""):
    try:
        service = get_calendar_service()
        tz = ZoneInfo(TIMEZONE)
        if appointment_dt.tzinfo is None:
            appointment_dt = appointment_dt.replace(tzinfo=tz)
        end_dt = appointment_dt + timedelta(minutes=duration_minutes)
        event = {
            "summary": f"HVAC - {service_type} | {customer_name}",
            "description": f"Customer: {customer_name}\nPhone: {customer_phone}\nService: {service_type}\nNotes: {notes}",
            "start": {"dateTime": appointment_dt.isoformat(), "timeZone": TIMEZONE},
            "end": {"dateTime": end_dt.isoformat(), "timeZone": TIMEZONE},
        }
        result = service.events().insert(calendarId=CALENDAR_ID, body=event).execute()
        print(f"[CALENDAR] Event created: {result.get('htmlLink')}")
        return {"success": True, "event_id": result.get("id"), "event_link": result.get("htmlLink")}
    except Exception as e:
        print(f"[CALENDAR] Error: {e}")
        return {"success": False, "error": str(e)}

if __name__ == "__main__":
    test_dt = datetime.now().replace(hour=10, minute=0, second=0, microsecond=0) + timedelta(days=1)
    print(book_appointment("Test Customer", "+15551234567", "AC Repair", test_dt))
