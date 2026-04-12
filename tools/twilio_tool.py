import os
from twilio.rest import Client

def send_sms(to: str, body: str) -> bool:
    try:
        client = Client(
            os.getenv("TWILIO_ACCOUNT_SID"),
            os.getenv("TWILIO_AUTH_TOKEN"),
        )
        client.messages.create(
            to=to,
            from_=os.getenv("TWILIO_PHONE_NUMBER"),
            body=body,
        )
        print(f"[SMS] Sent to {to}")
        return True
    except Exception as e:
        print(f"[SMS] Failed: {e}")
        return False