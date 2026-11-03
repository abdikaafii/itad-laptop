"""Configuration, read from environment variables only. No secrets live in the code."""
import os
import secrets

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
WEBSITE_DIR = os.path.dirname(BACKEND_DIR)


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


class Config:
    ENV = os.environ.get("APP_ENV", "development").strip().lower()
    PRODUCTION = ENV == "production"

    # Database: a SQLite file path. (PostgreSQL support is on the roadmap, see documentation.)
    DATABASE = os.environ.get("ITAD_DATABASE", os.path.join(BACKEND_DIR, "instance", "itad.sqlite3"))
    UPLOAD_DIR = os.environ.get("ITAD_UPLOAD_DIR", os.path.join(BACKEND_DIR, "instance", "uploads"))

    # Signs login cookies and certificate MACs. Required in production; random per run in development.
    SECRET_KEY = os.environ.get("ITAD_SECRET_KEY", "")

    PUBLIC_URL = os.environ.get("ITAD_PUBLIC_URL", "http://127.0.0.1:5000").rstrip("/")

    # Outgoing email. If SMTP_HOST is empty, mail is saved to the email_outbox table instead.
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", "587"))
    SMTP_USER = os.environ.get("SMTP_USER", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "")
    SMTP_FROM = os.environ.get("SMTP_FROM", "no-reply@localhost")

    MAX_UPLOAD_BYTES = int(os.environ.get("ITAD_MAX_UPLOAD_BYTES", str(5 * 1024 * 1024)))
    MAX_LOGO_BYTES = 1 * 1024 * 1024
    LOGIN_MAX_FAILS = int(os.environ.get("ITAD_LOGIN_MAX_FAILS", "5"))
    LOGIN_LOCK_MINUTES = int(os.environ.get("ITAD_LOGIN_LOCK_MINUTES", "15"))
    SECURE_COOKIES = _bool("ITAD_SECURE_COOKIES", PRODUCTION)
    # Local development convenience: show saved emails at /dev/outbox. Keep OFF on any shared server.
    DEV_OUTBOX = _bool("ITAD_DEV_OUTBOX", False)
    # Set to 1 ONLY when a reverse proxy you control (Caddy/nginx) sits in front. Then the real visitor
    # address is read from X-Forwarded-For. Never set it when the app is directly on the internet.
    TRUST_PROXY = _bool("ITAD_TRUST_PROXY", False)

    @classmethod
    def resolve_secret(cls):
        if cls.SECRET_KEY:
            if len(cls.SECRET_KEY) < 32:
                raise RuntimeError("ITAD_SECRET_KEY must be at least 32 characters.")
            return cls.SECRET_KEY
        if cls.PRODUCTION:
            raise RuntimeError("ITAD_SECRET_KEY is required when APP_ENV=production.")
        return secrets.token_hex(32)  # development only: sessions reset on every restart
