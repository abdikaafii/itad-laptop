"""Tiny database layer: one SQLite connection per request, plus a migration runner."""
import os
import sqlite3
from datetime import datetime, timezone

from flask import current_app, g

MIGRATIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))),
    "database", "migrations")


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def connect(path):
    if path != ":memory:":
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    con = sqlite3.connect(path, timeout=15)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys = ON")
    con.execute("PRAGMA journal_mode = WAL")
    return con


def get_db():
    if "db" not in g:
        g.db = connect(current_app.config["DATABASE"])
    return g.db


def close_db(exc=None):
    """Save the request's changes if it finished normally; undo them if the code crashed."""
    con = g.pop("db", None)
    if con is not None:
        try:
            if exc is None:
                con.commit()
            else:
                con.rollback()
        finally:
            con.close()


def migrate(path):
    """Apply every migrations/*.sql file that has not been applied yet."""
    con = connect(path)
    try:
        con.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {r["name"] for r in con.execute("SELECT name FROM schema_migrations")}
        applied = []
        for name in sorted(os.listdir(MIGRATIONS_DIR)):
            if not name.endswith(".sql") or name in done:
                continue
            with open(os.path.join(MIGRATIONS_DIR, name), encoding="utf-8") as f:
                sql = f.read()
            con.executescript(sql)
            con.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                        (name, utcnow()))
            con.commit()
            applied.append(name)
        return applied
    finally:
        con.close()


def one(sql, params=()):
    return get_db().execute(sql, params).fetchone()


def many(sql, params=()):
    return get_db().execute(sql, params).fetchall()


def run(sql, params=()):
    cur = get_db().execute(sql, params)
    return cur.lastrowid
