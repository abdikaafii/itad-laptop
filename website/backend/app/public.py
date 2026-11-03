"""Public pages: home page, demo request, and the certificate verification page."""
import re

from flask import Blueprint, flash, g, redirect, render_template, request, url_for

from .certificates.service import verify_certificate
from .core.audit import log
from .core.db import one, run, utcnow
from .core.security import rate_limit
from .core.util import form

bp = Blueprint("public", __name__)
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")


@bp.route("/")
def index():
    return render_template("public/index.html")


@bp.route("/request-demo", methods=["POST"])
@rate_limit("demo", 5, 3600)
def request_demo():
    name, email, company, msg = form("name", 120), form("email", 254).lower(), form("company", 120), form("message", 1500)
    if not name or not EMAIL_RE.match(email):
        flash("Please enter your name and a valid email address.", "error")
        return redirect(url_for("public.index") + "#contact")
    run("INSERT INTO demo_requests (name, email, company, message, created_at) VALUES (?,?,?,?,?)",
        (name, email, company, msg, utcnow()))
    log("demo.requested", "demo_request", "", "%s <%s>" % (name, email), actor="website")
    flash("Thank you. We have your request and will be in touch.", "ok")
    return redirect(url_for("public.index") + "#contact")


@bp.route("/verify", methods=["GET", "POST"])
@rate_limit("verify", 30, 60)
def verify_form():
    number = (request.values.get("number") or "").strip().upper()[:40]
    if request.method == "POST" and number:
        if not re.fullmatch(r"[A-Z0-9-]{6,40}", number):
            flash("That does not look like a certificate number.", "error")
            return render_template("public/verify.html", result=None, number=number), 400
        return redirect(url_for("public.verify_number", number=number))
    return render_template("public/verify.html", result=None, number=number)


@bp.route("/verify/<number>")
@rate_limit("verify", 30, 60)
def verify_number(number):
    number = number.strip().upper()[:40]
    result = verify_certificate(number) if re.fullmatch(r"[A-Z0-9-]{6,40}", number) else \
        {"state": "not_found", "cert": None, "content": None}
    status = 200 if result["state"] != "not_found" else 404
    return render_template("public/verify.html", result=result, number=number), status


@bp.route("/healthz")
def healthz():
    one("SELECT 1")
    return {"status": "ok"}
