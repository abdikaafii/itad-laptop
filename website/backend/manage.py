#!/usr/bin/env python3
"""Command-line helper for the ITAD Platform.

  python3 manage.py init-db                     create / update the database
  python3 manage.py create-superadmin EMAIL     create the platform owner (password asked, or generated)
  python3 manage.py seed-demo                   add clearly-labelled DEMONSTRATION data and logins
  python3 manage.py run [--port 5000]           development server on 127.0.0.1 only
"""
import argparse
import getpass
import os
import secrets
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from app import create_app  # noqa: E402
from app.core import security as S  # noqa: E402
from app.core.db import one, run, utcnow  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="ITAD Platform management")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    sa = sub.add_parser("create-superadmin")
    sa.add_argument("email")
    sa.add_argument("--name", default="Platform Administrator")
    sub.add_parser("seed-demo")
    rn = sub.add_parser("run")
    rn.add_argument("--port", type=int, default=5000)
    a = ap.parse_args()

    app = create_app()
    if a.cmd == "init-db":
        print("Database ready at", app.config["DATABASE"])
    elif a.cmd == "create-superadmin":
        with app.app_context():
            email = a.email.strip().lower()
            if one("SELECT 1 FROM users WHERE email = ?", (email,)):
                print("That email already has an account.")
                return 1
            pw = os.environ.get("ITAD_SUPERADMIN_PASSWORD") or getpass.getpass("Password (empty = generate one): ")
            generated = False
            if not pw:
                pw, generated = secrets.token_urlsafe(14) + "Aa1", True
            weak = S.password_problems(pw)
            if weak:
                print("Password needs " + ", ".join(weak) + ".")
                return 1
            run("INSERT INTO users (company_id, email, full_name, role, password_hash, email_verified, created_at) "
                "VALUES (NULL,?,?,?,?,1,?)", (email, a.name, "super_admin", S.hash_password(pw), utcnow()))
            from app.core.db import get_db
            get_db().commit()
            print("Super administrator created:", email)
            if generated:
                print("Generated password (shown once):", pw)
    elif a.cmd == "seed-demo":
        from app.seed import seed_demo, write_credentials
        with app.app_context():
            from app.core.db import get_db
            inst = os.path.join(HERE, "instance")
            creds = seed_demo(inst)
            get_db().commit()
            path = os.path.join(inst, "demo_credentials.txt")
            write_credentials(creds, path)
            print("Demo data created: %d laptops, %d certificates." % (
                one("SELECT COUNT(*) FROM assets WHERE company_id = ?", (creds["_company_id"],))[0],
                creds["_certificates"]))
            print("Logins saved to", path)
    elif a.cmd == "run":
        if app.config["PRODUCTION"]:
            print("Use a production server (gunicorn wsgi:app), not this development server.")
            return 1
        app.run(host="127.0.0.1", port=a.port, debug=False)
    return 0


if __name__ == "__main__":
    sys.exit(main())
