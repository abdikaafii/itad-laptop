"""Registration, email verification, login/logout, password reset and profile."""
import re
from datetime import datetime, timedelta, timezone

from flask import (Blueprint, abort, current_app, flash, g, redirect, render_template, request,
                   url_for)

from ..core import security as S
from ..core.audit import log
from ..core.db import many, one, run, utcnow
from ..core.mailer import send_email

bp = Blueprint("authentication", __name__)

EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,255}\.[^@\s]{2,}$")
_DUMMY_HASH = S.hash_password("not-a-real-password-0")


def _clean(name, limit=120):
    return (request.form.get(name) or "").strip()[:limit]


def send_verification(user_id, email, name):
    tok = S.make_auth_token(user_id, "verify_email", hours=48)
    link = "%s%s" % (current_app.config["PUBLIC_URL"], url_for("authentication.verify_email", token=tok))
    send_email(email, "Verify your email address",
               "Hello %s,\n\nPlease confirm your email address to activate your ITAD Platform account:\n\n%s\n\n"
               "The link works for 48 hours. If you did not ask for this, you can ignore this message.\n"
               % (name, link))


def send_password_link(user_id, email, name, invited=False):
    tok = S.make_auth_token(user_id, "reset_password", hours=48 if invited else 2)
    link = "%s%s" % (current_app.config["PUBLIC_URL"], url_for("authentication.reset_password", token=tok))
    if invited:
        subject = "You have been invited to the ITAD Platform"
        body = ("Hello %s,\n\nAn account has been created for you. Choose your password here:\n\n%s\n\n"
                "The link works for 48 hours.\n" % (name, link))
    else:
        subject = "Reset your password"
        body = ("Hello %s,\n\nUse this link to choose a new password:\n\n%s\n\n"
                "It works for 2 hours. If you did not ask for this, ignore this message; your "
                "password has not changed.\n" % (name, link))
    send_email(email, subject, body)


@bp.route("/register", methods=["GET", "POST"])
@S.rate_limit("register", 12, 3600)
def register():
    if g.user:
        return redirect(url_for("dashboard.home"))
    if request.method == "POST":
        company, name, email = _clean("company"), _clean("full_name"), _clean("email", 254).lower()
        pw, pw2 = request.form.get("password") or "", request.form.get("confirm") or ""
        errors = []
        if not company:
            errors.append("Enter your company name.")
        if not name:
            errors.append("Enter your name.")
        if not EMAIL_RE.match(email):
            errors.append("Enter a valid email address.")
        if pw != pw2:
            errors.append("The two passwords do not match.")
        weak = S.password_problems(pw)
        if weak:
            errors.append("Password needs " + ", ".join(weak) + ".")
        if errors:
            for e in errors:
                flash(e, "error")
            return render_template("public/register.html", form=request.form), 400
        if not one("SELECT 1 FROM users WHERE email = ?", (email,)):
            cid = run("INSERT INTO companies (name, legal_name, email, created_at) VALUES (?,?,?,?)",
                      (company, company, email, utcnow()))
            uid = run("INSERT INTO users (company_id, email, full_name, role, password_hash, created_at) "
                      "VALUES (?,?,?,?,?,?)", (cid, email, name, "company_admin", S.hash_password(pw), utcnow()))
            log("company.registered", "company", cid, company, company_id=cid, actor=email)
            send_verification(uid, email, name)
        # Same answer whether or not the address was already registered (no account enumeration).
        return render_template("public/message.html", title="Check your email",
                               message="If that address can be registered, we have sent a verification "
                                       "link to it. Open the link to activate your account.")
    return render_template("public/register.html", form={})


@bp.route("/verify-email/<token>")
def verify_email(token):
    uid = S.consume_auth_token(token, "verify_email")
    if not uid:
        return render_template("public/message.html", title="Link not valid",
                               message="This verification link is invalid or has expired. "
                                       "Request a new one from the sign-in page."), 400
    run("UPDATE users SET email_verified = 1 WHERE id = ?", (uid,))
    log("user.email_verified", "user", uid, actor="email link")
    flash("Email verified. You can sign in now.", "ok")
    return redirect(url_for("authentication.login"))


@bp.route("/resend-verification", methods=["POST"])
def resend_verification():
    email = _clean("email", 254).lower()
    u = one("SELECT * FROM users WHERE email = ? AND email_verified = 0 AND is_active = 1", (email,))
    if u:
        send_verification(u["id"], u["email"], u["full_name"])
    return render_template("public/message.html", title="Check your email",
                           message="If that account is waiting for verification, a new link has been sent.")


@bp.route("/login", methods=["GET", "POST"])
def login():
    if g.user:
        return redirect(url_for("dashboard.home"))
    nxt = S.safe_next(request.values.get("next", ""))
    if request.method == "POST":
        email, pw = _clean("email", 254).lower(), request.form.get("password") or ""
        u = one("SELECT * FROM users WHERE email = ?", (email,))
        generic = "Email or password is not correct."
        if not u:
            S.verify_password(pw, _DUMMY_HASH)  # keep timing similar
            flash(generic, "error")
            return render_template("public/login.html", next=nxt), 401
        if u["locked_until"] and u["locked_until"] > utcnow():
            flash("Too many wrong attempts. Try again in a few minutes.", "error")
            return render_template("public/login.html", next=nxt), 429
        if not S.verify_password(pw, u["password_hash"]):
            fails = u["failed_logins"] + 1
            lock = ""
            if fails >= current_app.config["LOGIN_MAX_FAILS"]:
                lock = (datetime.now(timezone.utc) + timedelta(
                    minutes=current_app.config["LOGIN_LOCK_MINUTES"])).strftime("%Y-%m-%d %H:%M:%S")
                fails = 0
                log("user.locked", "user", u["id"], "too many failed sign-ins", company_id=u["company_id"],
                    actor=email)
            run("UPDATE users SET failed_logins = ?, locked_until = ? WHERE id = ?", (fails, lock, u["id"]))
            flash(generic, "error")
            return render_template("public/login.html", next=nxt), 401
        if not u["email_verified"]:
            flash("Please verify your email address first. Check your inbox for the link.", "error")
            return render_template("public/login.html", next=nxt, unverified=email), 403
        if not u["is_active"]:
            flash("This account has been switched off. Contact your administrator.", "error")
            return render_template("public/login.html", next=nxt), 403
        if u["company_id"]:
            comp = one("SELECT is_active FROM companies WHERE id = ?", (u["company_id"],))
            if not comp or not comp["is_active"]:
                flash("Your company account is switched off.", "error")
                return render_template("public/login.html", next=nxt), 403
        run("UPDATE users SET failed_logins = 0, locked_until = '', last_login = ? WHERE id = ?",
            (utcnow(), u["id"]))
        S.login_user(u)
        log("user.login", "user", u["id"], company_id=u["company_id"], actor=email)
        return redirect(nxt or url_for("dashboard.home"))
    return render_template("public/login.html", next=nxt)


@bp.route("/logout", methods=["POST"])
def logout():
    if g.user:
        log("user.logout", "user", g.user["id"])
    S.logout_user()
    return redirect(url_for("public.index"))


@bp.route("/forgot-password", methods=["GET", "POST"])
@S.rate_limit("forgot", 12, 3600)
def forgot_password():
    if request.method == "POST":
        email = _clean("email", 254).lower()
        u = one("SELECT * FROM users WHERE email = ? AND is_active = 1", (email,))
        if u:
            send_password_link(u["id"], u["email"], u["full_name"])
            log("user.reset_requested", "user", u["id"], company_id=u["company_id"], actor=email)
        return render_template("public/message.html", title="Check your email",
                               message="If that address has an account, a reset link is on its way.")
    return render_template("public/forgot.html")


@bp.route("/reset-password/<token>", methods=["GET", "POST"])
def reset_password(token):
    row = one("SELECT * FROM auth_tokens WHERE token_hash = ? AND purpose = 'reset_password'",
              (S.sha256_hex(token),))
    valid = bool(row and not row["used_at"] and row["expires_at"] >= utcnow())
    if not valid:
        return render_template("public/message.html", title="Link not valid",
                               message="This password link is invalid, already used, or has expired. "
                                       "Request a new one."), 400
    if request.method == "POST":
        pw, pw2 = request.form.get("password") or "", request.form.get("confirm") or ""
        weak = S.password_problems(pw)
        if pw != pw2:
            flash("The two passwords do not match.", "error")
        elif weak:
            flash("Password needs " + ", ".join(weak) + ".", "error")
        else:
            uid = S.consume_auth_token(token, "reset_password")
            run("UPDATE users SET password_hash = ?, email_verified = 1, failed_logins = 0, "
                "locked_until = '', session_epoch = session_epoch + 1 WHERE id = ?",
                (S.hash_password(pw), uid))
            u = one("SELECT company_id, email FROM users WHERE id = ?", (uid,))
            log("user.password_reset", "user", uid, company_id=u["company_id"], actor=u["email"])
            flash("Password saved. Sign in with your new password.", "ok")
            return redirect(url_for("authentication.login"))
        return render_template("public/reset.html", token=token), 400
    return render_template("public/reset.html", token=token)


@bp.route("/profile", methods=["GET", "POST"])
@S.login_required
def profile():
    if request.method == "POST":
        action = request.form.get("action")
        if action == "name":
            name = _clean("full_name")
            if not name:
                flash("Enter your name.", "error")
            else:
                run("UPDATE users SET full_name = ? WHERE id = ?", (name, g.user["id"]))
                log("user.profile_updated", "user", g.user["id"])
                flash("Profile saved.", "ok")
        elif action == "password":
            cur, pw, pw2 = (request.form.get("current") or "", request.form.get("password") or "",
                            request.form.get("confirm") or "")
            weak = S.password_problems(pw)
            if not S.verify_password(cur, g.user["password_hash"]):
                flash("Your current password is not correct.", "error")
            elif pw != pw2:
                flash("The two new passwords do not match.", "error")
            elif weak:
                flash("Password needs " + ", ".join(weak) + ".", "error")
            else:
                run("UPDATE users SET password_hash = ?, session_epoch = session_epoch + 1 WHERE id = ?",
                    (S.hash_password(pw), g.user["id"]))
                log("user.password_changed", "user", g.user["id"])
                new = one("SELECT * FROM users WHERE id = ?", (g.user["id"],))
                S.login_user(new)  # keep this browser signed in, sign out all others
                flash("Password changed. Other devices have been signed out.", "ok")
        return redirect(url_for("authentication.profile"))
    return render_template("app/profile.html")


@bp.route("/dev/outbox")
def dev_outbox():
    """Local development only: shows emails that would have been sent (verification and reset links).
    Off unless ITAD_DEV_OUTBOX=1, and never available in production, because the page would expose
    reset links to anyone who can open it."""
    if current_app.config["PRODUCTION"] or not current_app.config["DEV_OUTBOX"]:
        abort(404)
    rows = many("SELECT * FROM email_outbox ORDER BY id DESC LIMIT 30")
    return render_template("public/outbox.html", rows=rows)
