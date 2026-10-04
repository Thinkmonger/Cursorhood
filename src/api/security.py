"""Network boundary for the local console.

The dashboard has no login: it trusts whoever can reach the port. That makes two
browser-borne attacks relevant, since any site the user visits can send requests
to 127.0.0.1:

* CSRF — a bodyless cross-origin ``POST`` is a CORS "simple request", so the
  browser sends it without a preflight and the handler runs. Reaching
  ``/api/bots/{id}/run-now`` that way starts a real trading cycle.
* DNS rebinding — an attacker domain re-resolved to 127.0.0.1 becomes
  same-origin with the console, which grants read access on top of write.

Host pinning closes the rebinding path; origin pinning closes CSRF.
"""
from __future__ import annotations

import logging
import os

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.types import ASGIApp

logger = logging.getLogger(__name__)

LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")
_WILDCARD_BINDS = ("0.0.0.0", "::", "")


def _split_env(name: str) -> list[str]:
    raw = os.environ.get(name, "")
    return [item.strip() for item in raw.split(",") if item.strip()]


def _dashboard_binding() -> tuple[str, int]:
    try:
        from src.settings.service import SettingsService

        cfg = SettingsService().read_app()
        return str(cfg.dashboard_host or "127.0.0.1"), int(cfg.dashboard_port or 8765)
    except Exception:
        return "127.0.0.1", 8765


def allowed_hosts() -> list[str]:
    """Hostnames accepted in the Host header, port stripped by the middleware."""
    host, _port = _dashboard_binding()
    hosts = set(LOOPBACK_HOSTS)
    if host not in _WILDCARD_BINDS:
        hosts.add(host)
    hosts.update(_split_env("DASHBOARD_ALLOWED_HOSTS"))
    return sorted(hosts)


def allowed_origins() -> set[str]:
    """Origins allowed to issue state-changing or websocket requests."""
    host, port = _dashboard_binding()
    names = set(LOOPBACK_HOSTS) - {"::1"}
    if host not in _WILDCARD_BINDS:
        names.add(host)
    origins = {f"http://{name}:{port}" for name in names}
    origins |= {f"https://{name}:{port}" for name in names}
    origins.update(_split_env("DASHBOARD_ALLOWED_ORIGINS"))
    return origins


def is_allowed_origin(origin: str | None) -> bool:
    """Absent Origin is fine (same-origin navigation, curl); a foreign one is not."""
    if not origin:
        return True
    return origin in allowed_origins()


class OriginGuardMiddleware(BaseHTTPMiddleware):
    """Reject requests carrying a foreign Origin header."""

    def __init__(self, app: ASGIApp) -> None:
        super().__init__(app)

    async def dispatch(self, request: Request, call_next) -> Response:
        origin = request.headers.get("origin")
        if not is_allowed_origin(origin):
            logger.warning(
                "Blocked cross-origin %s %s from origin %s",
                request.method,
                request.url.path,
                origin,
            )
            return JSONResponse(
                {"detail": "Cross-origin requests are not allowed."},
                status_code=403,
            )
        return await call_next(request)


_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}

_CACHEABLE_PREFIXES = ("/static/", "/locales/")


class SecurityHeadersMiddleware(BaseHTTPMiddleware):
    """Browser hardening for a no-login local console."""

    async def dispatch(self, request: Request, call_next) -> Response:
        response = await call_next(request)
        for key, value in _SECURITY_HEADERS.items():
            response.headers.setdefault(key, value)
        path = request.url.path
        if path.startswith(_CACHEABLE_PREFIXES):
            response.headers.setdefault(
                "Cache-Control",
                "public, max-age=86400, stale-while-revalidate=604800",
            )
        return response


def install_security(app) -> None:
    """Pin Host and Origin, then stamp security and static-cache headers."""
    from starlette.middleware.trustedhost import TrustedHostMiddleware

    app.add_middleware(SecurityHeadersMiddleware)
    app.add_middleware(OriginGuardMiddleware)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=allowed_hosts())
