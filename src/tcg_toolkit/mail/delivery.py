"""SMTP delivery for account verification and subscription notifications."""
from email.message import EmailMessage
import os
import smtplib
import ssl

from .templates import VERIFICATION_SUBJECT, verification_html, verification_text


class SMTPMailer:
    def __init__(self):
        self.host = os.getenv("TCG_SMTP_HOST", "")
        self.username = os.getenv("TCG_SMTP_USERNAME", "")
        self.password = os.getenv("TCG_SMTP_PASSWORD", "")
        self.sender = os.getenv("TCG_SMTP_FROM", "")
        self.mode = os.getenv("TCG_SMTP_TLS", "starttls")
        try:
            self.port = int(os.getenv("TCG_SMTP_PORT") or ("465" if self.mode == "ssl" else "587"))
        except ValueError:
            self.port = 0
        self.configured = bool(self.host and self.sender and 0 < self.port < 65536
                               and self.mode in ("ssl", "starttls")
                               and bool(self.username) == bool(self.password)
                               and "\n" not in self.sender and "\r" not in self.sender)

    def send_code(self, email, code, language, purpose):
        message = EmailMessage()
        message["From"] = self.sender
        message["To"] = email
        # Every code email is bilingual (Chinese, then English); language stays for the API contract.
        message["Subject"] = VERIFICATION_SUBJECT
        message.set_content(verification_text(code, purpose))
        message.add_alternative(verification_html(code, purpose), subtype='html')
        self.send_message(message)

    def send_message(self, message):
        if not self.configured:
            raise ValueError("SMTP is not configured")
        if "From" not in message:
            message["From"] = self.sender
        context = ssl.create_default_context()
        if self.mode == "ssl":
            connection = smtplib.SMTP_SSL(self.host, self.port, timeout=10, context=context)
        else:
            connection = smtplib.SMTP(self.host, self.port, timeout=10)
        with connection as smtp:
            if self.mode == "starttls":
                smtp.starttls(context=context)
            if self.username:
                smtp.login(self.username, self.password)
            if smtp.send_message(message):
                raise smtplib.SMTPException("Recipient refused")
