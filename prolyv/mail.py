"""Outgoing e-mail (sign-in links) over SMTP. Nothing is sent when SMTP is not configured."""

from __future__ import annotations

import logging
import smtplib
from email.message import EmailMessage

from prolyv.config import Settings

log = logging.getLogger("prolyv.mail")


def send_mail(settings: Settings, to: str, subject: str, text: str) -> None:
    message = EmailMessage()
    message["From"] = settings.smtp_from
    message["To"] = to
    message["Subject"] = subject
    message.set_content(text)
    with smtplib.SMTP(settings.smtp_host, settings.smtp_port, timeout=20) as smtp:
        if settings.smtp_tls:
            smtp.starttls()
        if settings.smtp_user:
            smtp.login(settings.smtp_user, settings.smtp_password)
        smtp.send_message(message)
    log.info("sent %r to %s", subject, to)
