"""Small helpers shared by the route modules."""
from flask import request

from .db import many, one


def form(name, limit=200):
    return (request.form.get(name) or "").strip()[:limit]


def arg(name, limit=100):
    return (request.args.get(name) or "").strip()[:limit]


def int_or_none(value):
    try:
        v = int(value)
        return v if v > 0 else None
    except (TypeError, ValueError):
        return None


def like(term):
    """Escape a user search term for a LIKE ... ESCAPE '\\' clause."""
    t = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return "%" + t + "%"


def paginate(count_sql, rows_sql, params, per_page=25):
    page = max(1, int_or_none(request.args.get("page")) or 1)
    total = one(count_sql, params)[0]
    pages = max(1, (total + per_page - 1) // per_page)
    page = min(page, pages)
    rows = many(rows_sql + " LIMIT ? OFFSET ?", list(params) + [per_page, (page - 1) * per_page])
    return {"rows": rows, "page": page, "pages": pages, "total": total, "per_page": per_page}


def query_string(**overrides):
    """Current query string with some values replaced (used by pagination links)."""
    from urllib.parse import urlencode
    args = {k: v for k, v in request.args.items() if k != "page" and v}
    args.update({k: v for k, v in overrides.items() if v not in (None, "")})
    return urlencode(args)
