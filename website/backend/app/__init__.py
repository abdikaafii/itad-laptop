"""ITAD Platform web application (Flask)."""
import os

from flask import Flask, g, render_template, request

from .core import security
from .core.config import BACKEND_DIR, WEBSITE_DIR, Config
from .core.db import close_db, migrate


def _dev_secret(instance_dir):
    """Development only: keep one secret between restarts so issued certificates still verify."""
    path = os.path.join(instance_dir, "dev_secret.key")
    if os.path.exists(path):
        with open(path) as f:
            return f.read().strip()
    import secrets
    os.makedirs(instance_dir, exist_ok=True)
    tok = secrets.token_hex(32)
    with open(path, "w") as f:
        f.write(tok)
    os.chmod(path, 0o600)
    return tok


def create_app(overrides=None):
    app = Flask(__name__,
                template_folder=os.path.join(WEBSITE_DIR, "frontend", "templates"),
                static_folder=os.path.join(WEBSITE_DIR, "frontend", "static"))
    app.config.from_object(Config)
    if overrides:
        app.config.update(overrides)
    if not app.config.get("SECRET_KEY"):
        if app.config["PRODUCTION"]:
            Config.resolve_secret()  # raises a clear error
        app.config["SECRET_KEY"] = _dev_secret(os.path.join(BACKEND_DIR, "instance"))
    elif len(app.config["SECRET_KEY"]) < 32:
        raise RuntimeError("ITAD_SECRET_KEY must be at least 32 characters.")

    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE="Lax",
                      SESSION_COOKIE_SECURE=app.config["SECURE_COOKIES"],
                      MAX_CONTENT_LENGTH=app.config["MAX_UPLOAD_BYTES"],
                      SESSION_COOKIE_NAME="itad_session")
    os.makedirs(app.config["UPLOAD_DIR"], exist_ok=True)
    if app.config.get("TRUST_PROXY"):
        from werkzeug.middleware.proxy_fix import ProxyFix
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1)
    migrate(app.config["DATABASE"])
    app.teardown_appcontext(close_db)

    @app.before_request
    def _guard():
        security.load_user()
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            # The engine API uses bearer tokens, not cookies, so CSRF does not apply to it.
            if not request.path.startswith("/api/v1/engine/"):
                security.check_csrf()

    app.after_request(security.add_security_headers)

    app.jinja_env.globals["csrf_token"] = security.csrf_token

    @app.context_processor
    def _ctx():
        return {"csrf_token": security.csrf_token, "me": g.get("user"), "my_company": g.get("company"),
                "ROLE_LABELS": security.ROLE_LABELS, "can_manage": security.can_manage_company()}

    from .authentication.routes import bp as auth_bp
    from .public import bp as public_bp
    from .assets.routes import bp as assets_bp
    from .certificates.routes import bp as certs_bp
    from .dashboard.routes import bp as dash_bp
    from .reports.routes import bp as reports_bp
    from .admin import bp as admin_bp
    from .integrations.api import bp as api_bp
    for b in (public_bp, auth_bp, dash_bp, assets_bp, certs_bp, reports_bp, admin_bp, api_bp):
        app.register_blueprint(b)

    @app.errorhandler(400)
    def _e400(e):
        return render_template("public/error.html", code=400, title="Request not accepted",
                               message=getattr(e, "description", "")), 400

    @app.errorhandler(403)
    def _e403(e):
        return render_template("public/error.html", code=403, title="Not allowed",
                               message="Your account does not have access to this page."), 403

    @app.errorhandler(404)
    def _e404(e):
        return render_template("public/error.html", code=404, title="Page not found",
                               message="That page does not exist, or you do not have access to it."), 404

    @app.errorhandler(413)
    def _e413(e):
        return render_template("public/error.html", code=413, title="File too large",
                               message="The file you sent is larger than the allowed limit."), 413

    @app.errorhandler(429)
    def _e429(e):
        return render_template("public/error.html", code=429, title="Slow down",
                               message=getattr(e, "description", "")), 429

    @app.errorhandler(500)
    def _e500(e):
        return render_template("public/error.html", code=500, title="Something went wrong",
                               message="The error has been recorded. Please try again."), 500

    return app
