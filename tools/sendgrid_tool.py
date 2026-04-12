import os
from sendgrid import SendGridAPIClient
from sendgrid.helpers.mail import Mail

def send_email(to: str, subject: str, html_content: str) -> bool:
    try:
        msg = Mail(
            from_email=os.getenv("SENDGRID_FROM_EMAIL"),
            to_emails=to,
            subject=subject,
            html_content=html_content,
        )
        sg = SendGridAPIClient(os.getenv("SENDGRID_API_KEY"))
        sg.send(msg)
        print(f"[EMAIL] Sent to {to}")
        return True
    except Exception as e:
        print(f"[EMAIL] Failed: {e}")
        return False