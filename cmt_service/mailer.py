"""Sending email (T11). Which way it is sent is chosen with MAIL_BACKEND:

- "console" (default): nothing leaves the computer. The message is put in OUTBOX (a list), good for
  tests and for a demo without a mail server.
- "smtp": a real mail server. Set SMTP_HOST, SMTP_PORT (default 587), SMTP_USER, SMTP_PASSWORD,
  MAIL_FROM. STARTTLS is used unless SMTP_STARTTLS=false.
"""
import os
import smtplib
from email.message import EmailMessage

OUTBOX = []


def one_line(text):
    """A header must be one line; a newline in a title could otherwise add extra headers."""
    return " ".join(str(text).split())


def send_email(to, subject, body, attachments=()):
    """Send one email. `attachments` is a list of (file name, text). Raises when sending fails."""
    message = EmailMessage()
    message["From"] = os.environ.get("MAIL_FROM", "cmt@localhost")
    message["To"] = one_line(to)
    message["Subject"] = one_line(subject)
    message.set_content(body)
    for name, text in attachments:
        message.add_attachment(text.encode("utf-8"), maintype="text", subtype="plain", filename=name)

    if os.environ.get("MAIL_BACKEND", "console") == "smtp":
        with smtplib.SMTP(os.environ["SMTP_HOST"], int(os.environ.get("SMTP_PORT", 587)), timeout=10) as server:
            if os.environ.get("SMTP_STARTTLS", "true").lower() != "false":
                server.starttls()
            if os.environ.get("SMTP_USER"):
                server.login(os.environ["SMTP_USER"], os.environ.get("SMTP_PASSWORD", ""))
            server.send_message(message)
    else:
        OUTBOX.append(message)
