"""ASGI application:  uvicorn app.main:app  (run from website/backend)."""
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import FileResponse, JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from . import config
from .api import build_routes
from .db import get_db
from .seed import run as seed_run

CSP = ("default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
       "font-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'; object-src 'none'")


class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        resp.headers["Content-Security-Policy"] = CSP
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["X-Frame-Options"] = "DENY"
        resp.headers["Referrer-Policy"] = "same-origin"
        resp.headers["Permissions-Policy"] = "camera=(), microphone=(), geolocation=()"
        if config.COOKIE_SECURE:
            resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp


def _page(name):
    async def handler(request):
        return FileResponse(config.FRONTEND_DIR / name, media_type="text/html")
    return handler


async def api_not_found(request):
    return JSONResponse({"detail": "Not found."}, status_code=404)


def create_app():
    get_db()
    seed_run()
    routes = build_routes() + [
        Route("/api/{rest:path}", api_not_found),
        Route("/", _page("index.html")),
        Route("/app", _page("app.html")),
        Route("/verify", _page("verify.html")),
        Route("/verify/{number}", _page("verify.html")),
        Mount("/static", StaticFiles(directory=str(config.FRONTEND_DIR / "static")), name="static"),
    ]
    app = Starlette(routes=routes)
    app.add_middleware(SecurityHeaders)
    return app


app = create_app()
