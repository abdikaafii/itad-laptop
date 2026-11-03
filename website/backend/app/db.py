"""Small database layer: SQLite for development/tests, PostgreSQL for production.

Queries are written once with `?` placeholders. For PostgreSQL they are
translated to `%s`. Rows come back as plain dicts.

NOTE: the PostgreSQL path (psycopg2) is written to the same interface but
could not be exercised in the build environment (no network to install the
driver). It is covered by docker/docker-compose.yml for you to try.
"""
import re
import sqlite3
import threading
from datetime import datetime, timezone

from . import config


def utcnow():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


class DB:
    def __init__(self, url):
        self.url = url
        self.lock = threading.RLock()
        if url.startswith("sqlite"):
            self.dialect = "sqlite"
            path = url.split("sqlite:///", 1)[1]
            if path != ":memory:":
                import os
                os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False,
                                        isolation_level=None)
            self.conn.row_factory = sqlite3.Row
            self.conn.execute("PRAGMA foreign_keys = ON")
            self.conn.execute("PRAGMA journal_mode = WAL" if path != ":memory:"
                              else "PRAGMA journal_mode = MEMORY")
        elif url.startswith(("postgres://", "postgresql://")):
            self.dialect = "postgres"
            import psycopg2                      # noqa: imported lazily
            import psycopg2.extras
            self.conn = psycopg2.connect(url)
            self.conn.autocommit = True
            self._extras = psycopg2.extras
        else:
            raise ValueError("Unsupported ITAD_DATABASE_URL: %s" % url.split(":")[0])

    # ---- helpers -------------------------------------------------------
    def _sql(self, sql):
        return sql.replace("?", "%s") if self.dialect == "postgres" else sql

    def _cur(self):
        if self.dialect == "postgres":
            return self.conn.cursor(cursor_factory=self._extras.RealDictCursor)
        return self.conn.cursor()

    @staticmethod
    def _clean(row):
        d = dict(row)
        for k, v in d.items():
            if isinstance(v, memoryview):
                d[k] = bytes(v)
        return d

    def query(self, sql, params=()):
        with self.lock:
            cur = self._cur()
            cur.execute(self._sql(sql), tuple(params))
            rows = [self._clean(r) for r in cur.fetchall()]
            cur.close()
            return rows

    def one(self, sql, params=()):
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def execute(self, sql, params=()):
        with self.lock:
            cur = self._cur()
            cur.execute(self._sql(sql), tuple(params))
            n = cur.rowcount
            cur.close()
            return n

    def insert(self, table, values):
        cols = list(values)
        marks = ",".join("?" for _ in cols)
        sql = "INSERT INTO %s (%s) VALUES (%s)" % (table, ",".join(cols), marks)
        with self.lock:
            cur = self._cur()
            if self.dialect == "postgres":
                cur.execute(self._sql(sql + " RETURNING id"), tuple(values[c] for c in cols))
                new_id = cur.fetchone()["id"]
            else:
                cur.execute(sql, tuple(values[c] for c in cols))
                new_id = cur.lastrowid
            cur.close()
            return new_id

    def update(self, table, row_id, values):
        sets = ",".join("%s = ?" % c for c in values)
        self.execute("UPDATE %s SET %s WHERE id = ?" % (table, sets),
                     list(values.values()) + [row_id])

    def transaction(self):
        return _Tx(self)

    # ---- migrations ----------------------------------------------------
    def migrate(self):
        self.execute("CREATE TABLE IF NOT EXISTS schema_migrations "
                     "(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
        done = {r["name"] for r in self.query("SELECT name FROM schema_migrations")}
        files = sorted(p for p in config.MIGRATIONS_DIR.glob("*.sql"))
        applied = []
        for p in files:
            parts = p.name[:-4].split(".")
            if len(parts) == 2 and parts[1] != self.dialect:
                continue                         # a script for the other database
            if p.name in done:
                continue
            text = p.read_text(encoding="utf-8")
            pk = ("INTEGER PRIMARY KEY AUTOINCREMENT" if self.dialect == "sqlite"
                  else "SERIAL PRIMARY KEY")
            blob = "BLOB" if self.dialect == "sqlite" else "BYTEA"
            text = text.replace("{PK}", pk).replace("{BLOB}", blob)
            with self.lock:
                if self.dialect == "sqlite":
                    self.conn.executescript("BEGIN;\n%s\nCOMMIT;" % text)
                else:
                    cur = self.conn.cursor()
                    cur.execute(text)
                    cur.close()
            self.execute("INSERT INTO schema_migrations (name, applied_at) VALUES (?, ?)",
                         (p.name, utcnow()))
            applied.append(p.name)
        return applied


class _Tx:
    """BEGIN IMMEDIATE ... COMMIT, holding the lock for the whole block."""

    def __init__(self, db):
        self.db = db

    def __enter__(self):
        self.db.lock.acquire()
        if self.db.dialect == "sqlite":
            self.db.conn.execute("BEGIN IMMEDIATE")
        else:
            self.db.conn.autocommit = False
        return self.db

    def __exit__(self, et, ev, tb):
        try:
            if et is None:
                self.db.conn.commit()
            else:
                self.db.conn.rollback()
        finally:
            if self.db.dialect == "postgres":
                self.db.conn.autocommit = True
            self.db.lock.release()
        return False


_db = None


def get_db():
    global _db
    if _db is None:
        _db = DB(config.DATABASE_URL)
        _db.migrate()
    return _db


def reset_db(url=None):
    """Used by the tests to start from an empty database."""
    global _db
    _db = DB(url or config.DATABASE_URL)
    _db.migrate()
    return _db
