"""First-start setup: bootstrap super administrator and (development only)
clearly labelled DEMONSTRATION data.

Demo accounts exist only when ITAD_SEED_DEMO is on (default: off in production).
Their password comes from ITAD_DEMO_PASSWORD, or the documented development
default. Never use them on a real server.
"""
import os
from datetime import datetime, timedelta, timezone

from . import config
from .api import simulated_report
from .db import get_db, utcnow
from .security import hash_password
from .services import ingest_report, issue_certificate, refresh_asset

DEMO_PASSWORD = os.environ.get("ITAD_DEMO_PASSWORD", "Demo-Passw0rd!")
DEMO_ACCOUNTS = [
    ("super@demo.itad", "Demo Super Admin", "super_admin"),
    ("admin@demo.itad", "Demo Company Admin", "company_admin"),
    ("tech@demo.itad", "Demo Technician", "technician"),
    ("customer@demo.itad", "Demo Customer", "customer"),
]


def bootstrap_admin():
    db = get_db()
    if db.one("SELECT 1 AS x FROM users WHERE role = 'super_admin'"):
        return
    if config.BOOTSTRAP_ADMIN_EMAIL and config.BOOTSTRAP_ADMIN_PASSWORD:
        db.insert("users", {"company_id": None, "email": config.BOOTSTRAP_ADMIN_EMAIL.lower(), "name": "Super Administrator",
                            "role": "super_admin", "password_hash": hash_password(config.BOOTSTRAP_ADMIN_PASSWORD),
                            "email_verified": 1, "created_at": utcnow()})


def seed_demo():
    db = get_db()
    if db.one("SELECT 1 AS x FROM companies WHERE slug = 'demo-itad'"):
        return False
    cid = db.insert("companies", {"name": "Demo ITAD Ltd (DEMONSTRATION)", "slug": "demo-itad", "address": "1 Example Street, Drogheda, Ireland",
                                  "email": "hello@demo.itad", "phone": "+353 1 000 0000", "website": "https://example.invalid", "cert_prefix": "DEMO",
                                  "require_signed": 1, "created_at": utcnow()})
    ids = {}
    for email, name, role in DEMO_ACCOUNTS:
        ids[role] = db.insert("users", {"company_id": None if role == "super_admin" else cid, "email": email, "name": name, "role": role,
                                        "password_hash": hash_password(DEMO_PASSWORD), "email_verified": 1, "created_at": utcnow()})
    customers = [db.insert("customers", {"company_id": cid, "name": n, "contact_name": c, "email": e, "created_at": utcnow()})
                 for n, c, e in (("Acme Financial (demo)", "Pat Murphy", "pat@acme.invalid"), ("Northwind Schools (demo)", "Sam Byrne", "sam@northwind.invalid"))]
    db.update("users", ids["customer"], {"customer_id": customers[0]})
    jobs = [db.insert("jobs", {"company_id": cid, "customer_id": customers[i % 2], "job_ref": ref, "title": title, "status": st,
                               "technician_id": ids["technician"], "created_at": utcnow()})
            for i, (ref, title, st) in enumerate((("JOB-DEMO-0001", "Acme laptop refresh batch 1", "in_progress"),
                                                  ("JOB-DEMO-0002", "Northwind classroom laptops", "open"),
                                                  ("JOB-DEMO-0003", "Acme leavers returns", "open")))]
    models = [("HP", "EliteBook 840 G5", "Intel Core i5-8350U"), ("Dell", "Latitude 5420", "Intel Core i5-1145G7"),
              ("Lenovo", "ThinkPad T14", "AMD Ryzen 5 PRO 4650U"), ("HP", "ProBook 450 G8", "Intel Core i5-1135G7")]
    scenarios = ["pass", "pass", "pass", "fail", "incomplete", "blocked", "pass", "pass", None, None, "pass", None]
    admin = db.one("SELECT * FROM users WHERE id = ?", (ids["company_admin"],))
    tech = db.one("SELECT * FROM users WHERE id = ?", (ids["technician"],))
    now = datetime.now(timezone.utc)
    for i, scen in enumerate(scenarios):
        job_id = jobs[i % 3]
        job = db.one("SELECT * FROM jobs WHERE id = ?", (job_id,))
        mk, md, cpu = models[i % 4]
        created = (now - timedelta(days=(len(scenarios) - i) * 1.5)).strftime("%Y-%m-%dT%H:%M:%SZ")
        aid = db.insert("assets", {"company_id": cid, "customer_id": job["customer_id"], "job_id": job_id, "asset_tag": "AST-D%04d" % (4800 + i),
                                   "serial": "5CG8%03dDEMO" % (240 + i), "manufacturer": mk, "model": md, "cpu": cpu, "ram_gb": 16,
                                   "storage_summary": "256 GB NVMe SSD", "bios": "Demo BIOS 1.0", "os_info": "Windows 10 Pro", "chassis": "Laptop",
                                   "technician_id": ids["technician"], "status": "registered", "demo": 1, "created_at": created, "updated_at": created})
        if scen:
            asset = db.one("SELECT * FROM assets WHERE id = ?", (aid,))
            ingest_report(cid, simulated_report(asset, job, scen, tech["name"]), None, tech)
        else:
            refresh_asset(aid)
        db.execute("UPDATE assets SET created_at = ? WHERE id = ?", (created, aid))
    # a few demonstration certificates
    for er in db.query("SELECT e.id FROM erasures e JOIN assets a ON a.id = e.asset_id WHERE a.company_id = ? AND e.verified = 1 ORDER BY e.id LIMIT 3", (cid,)):
        issue_certificate(admin, er["id"])
    return True


def run():
    bootstrap_admin()
    if config.SEED_DEMO:
        seed_demo()
