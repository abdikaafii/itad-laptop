"""Settings. Everything comes from environment variables (see .env.example).

No secret is hardcoded. In production (ITAD_ENV=production) the app refuses
to start without ITAD_SECRET_KEY.
"""
import os
import secrets
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parents[2]          # .../website
DATA_DIR = Path(os.environ.get("ITAD_DATA_DIR", BASE_DIR / "data"))
FRONTEND_DIR = Path(os.environ.get("ITAD_FRONTEND_DIR", BASE_DIR / "frontend"))
MIGRATIONS_DIR = Path(os.environ.get("ITAD_MIGRATIONS_DIR",
                                     BASE_DIR / "database" / "migrations"))


def _bool(name, default=False):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


ENV = os.environ.get("ITAD_ENV", "development")
IS_PROD = ENV == "production"

# sqlite:///path.db  (development, tests)   or   postgresql://user:pw@host/db
DATABASE_URL = os.environ.get("ITAD_DATABASE_URL",
                              "sqlite:///%s" % (DATA_DIR / "itad_platform.db"))

SECRET_KEY = os.environ.get("ITAD_SECRET_KEY", "")
if not SECRET_KEY:
    if IS_PROD:
        raise RuntimeError("ITAD_SECRET_KEY must be set in production")
    SECRET_KEY = secrets.token_hex(32)       # dev only: sessions reset on restart

PUBLIC_BASE_URL = os.environ.get("ITAD_PUBLIC_URL", "http://localhost:8000").rstrip("/")
COOKIE_SECURE = _bool("ITAD_COOKIE_SECURE", IS_PROD)
SESSION_HOURS = int(os.environ.get("ITAD_SESSION_HOURS", "12"))
ALLOW_REGISTRATION = _bool("ITAD_ALLOW_REGISTRATION", True)
REQUIRE_EMAIL_VERIFICATION = _bool("ITAD_REQUIRE_EMAIL_VERIFICATION", True)
# Development only: put the one-time token in the API response so the flow
# can be tried without a mail server. Never enabled in production.
EXPOSE_DEV_TOKENS = _bool("ITAD_EXPOSE_DEV_TOKENS", not IS_PROD)

RATE_LIMIT = _bool("ITAD_RATE_LIMIT", True)     # per-IP limits on login, register, contact, verify
MAX_FAILED_LOGINS = 5
LOCKOUT_MINUTES = 15
MIN_PASSWORD_LEN = 10

# Platform key used to sign certificates (Ed25519). Generated on first start
# into DATA_DIR/keys/ unless ITAD_CERT_KEY_FILE points to your own PEM.
CERT_KEY_FILE = Path(os.environ.get("ITAD_CERT_KEY_FILE",
                                    DATA_DIR / "keys" / "platform_cert_key.pem"))

SMTP_HOST = os.environ.get("ITAD_SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("ITAD_SMTP_PORT", "587"))
SMTP_USER = os.environ.get("ITAD_SMTP_USER", "")
SMTP_PASSWORD = os.environ.get("ITAD_SMTP_PASSWORD", "")
SMTP_FROM = os.environ.get("ITAD_SMTP_FROM", "no-reply@localhost")

# First super administrator, created on start if no super admin exists.
BOOTSTRAP_ADMIN_EMAIL = os.environ.get("ITAD_BOOTSTRAP_ADMIN_EMAIL", "")
BOOTSTRAP_ADMIN_PASSWORD = os.environ.get("ITAD_BOOTSTRAP_ADMIN_PASSWORD", "")

# Demonstration data (clearly labelled) for development. Off in production.
SEED_DEMO = _bool("ITAD_SEED_DEMO", not IS_PROD)

TIMEZONE = "Europe/Dublin"
PRODUCT_NAME = "ITAD Seal Platform"
