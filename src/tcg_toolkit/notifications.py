"""Shared email delivery and subscription message composition."""
from datetime import datetime
from email.message import EmailMessage
import os
import smtplib
import ssl

from .email_templates import (VERIFICATION_SUBJECT, verification_html, verification_text,
                              digest_html, digest_subject, digest_titles, favorites_url)


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


def notification_recipient(user):
    if user is None:
        return None
    if user.notification_email and user.notification_email_verified_at is not None:
        return user.notification_email
    return user.email if user.email_verified_at is not None else None


def digest_message(recipient, email_id, run_date, changes):
    message = EmailMessage()
    message['To'] = recipient
    message['Subject'] = digest_subject(run_date)
    message['Message-ID'] = f'<{email_id}@tcg-toolkit.local>'
    # Bilingual like the HTML part: Chinese first, then English, line by line.
    zh_title, en_title = digest_titles(len(changes))
    text = [f'{run_date} {zh_title}（普通品相）', f'{run_date} {en_title} (normal condition)', '']
    for change in changes:
        before, after = change['before'], change['after']
        title = ' · '.join(filter(None, [after['name'], after.get('rarity'), after['card_number']]))
        price = f"价格 Price：{before['price']:,} → {after['price']:,} JPY"
        quantity = f"库存 Stock：{before['stock_quantity'] or 0:,} → {after['stock_quantity'] or 0:,}"
        checked = '检查时间 Checked at：' + datetime.fromtimestamp(change['checked_at']).astimezone().isoformat(timespec='seconds')
        url = after['product_url']
        text.extend([title, price, quantity, checked, url, ''])
    text.append(f'前往 TCG Toolkit 查看 · Open TCG Toolkit：{favorites_url()}')
    message.set_content('\n'.join(text))
    message.add_alternative(digest_html(run_date, changes), subtype='html')
    return message


