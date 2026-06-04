from __future__ import annotations

import logging
import os

import httpx

logger = logging.getLogger(__name__)
_ssl_verify_warning_logged = False


def ssl_verify_enabled() -> bool:
    value = os.environ.get("SSL_VERIFY", "true").strip().lower()
    return value not in ("0", "false", "no", "off")


def _log_ssl_verify_disabled_once() -> None:
    global _ssl_verify_warning_logged
    if _ssl_verify_warning_logged:
        return
    _ssl_verify_warning_logged = True
    logger.warning(
        "SSL_VERIFY is disabled — HTTPS certificate checks are off for this process"
    )


def async_client(**kwargs: object) -> httpx.AsyncClient:
    verify = ssl_verify_enabled()
    if not verify:
        _log_ssl_verify_disabled_once()
    timeout = kwargs.pop("timeout", 60)
    return httpx.AsyncClient(verify=verify, timeout=timeout, **kwargs)  # type: ignore[arg-type]