"""Send email by SMTP when configured; otherwise keep it in the email_outbox table."""
import smtplib
import ssl
from email.message import EmailMessage

from flask import current_app

from .db import run, utcnow


def send_email(to_addr, subject, body):
    cfg = current_app.config
    error = ""
    sent = 0
    if cfg["SMTP_HOST"]:
        try:
            msg = EmailMessage()
            msg["From"], msg["To"], msg["Subject"] = cfg["SMTP_FROM"], to_addr, subject
            msg.set_content(body)
            with smtplib.SMTP(cfg["SMTP_HOST"], cfg["SMTP_PORT"], timeout=15) as s:
                s.starttls(context=ssl.create_default_context())
                if cfg["SMTP_USER"]:
                    s.login(cfg["SMTP_USER"], cfg["SMTP_PASSWORD"])
                s.send_message(msg)
            sent = 1
        except Exception as ex:  # keep the message so nothing is lost
            error = str(ex)[:200]
    run("INSERT INTO email_outbox (to_addr, subject, body, created_at, sent, error) VALUES (?,?,?,?,?,?)",
        (to_addr, subject, body, utcnow(), sent, error))
    return sent == 1
