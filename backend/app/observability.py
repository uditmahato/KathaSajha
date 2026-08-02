"""Structured logging and request correlation.

Every log line carries a request_id (or job id) so one user's report of "my story
never finished" can be traced end to end across the API and the worker.
"""

import json
import logging
import sys
import time
import uuid
from contextvars import ContextVar

from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings

# Set per request in the API, per job in the worker.
correlation_id: ContextVar[str] = ContextVar("correlation_id", default="-")

_RESERVED = set(logging.LogRecord("", 0, "", 0, "", (), None).__dict__) | {
    "asctime",
    "message",
    "taskName",
}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created))
            + f".{int(record.msecs):03d}Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "cid": correlation_id.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # Anything passed via logger.info(..., extra={...}) rides along.
        for key, value in record.__dict__.items():
            if key not in _RESERVED and not key.startswith("_"):
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging() -> None:
    settings = get_settings()
    handler = logging.StreamHandler(sys.stdout)
    if settings.log_format == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(settings.log_level.upper())
    # uvicorn installs its own handlers; route them through ours.
    for name in ("uvicorn", "uvicorn.access", "uvicorn.error", "arq"):
        lg = logging.getLogger(name)
        lg.handlers[:] = []
        lg.propagate = True


class CorrelationMiddleware(BaseHTTPMiddleware):
    """Assigns a request id, exposes it on the response, and logs one line per
    request with method, path, status, and duration."""

    _logger = logging.getLogger("kathasajha.request")

    async def dispatch(self, request, call_next):
        incoming = request.headers.get("x-request-id", "")
        cid = incoming[:64] if incoming else uuid.uuid4().hex[:12]
        token = correlation_id.set(cid)
        start = time.perf_counter()
        status_code = 500
        try:
            response = await call_next(request)
            status_code = response.status_code
            response.headers["X-Request-ID"] = cid
            return response
        finally:
            duration_ms = round((time.perf_counter() - start) * 1000, 1)
            path = request.url.path
            # Health checks would drown the log at a 15s interval.
            if path != "/api/health":
                self._logger.info(
                    "request",
                    extra={
                        "method": request.method,
                        "path": path,
                        "status": status_code,
                        "duration_ms": duration_ms,
                    },
                )
            correlation_id.reset(token)


def set_correlation_id(value: str) -> None:
    """Used by the worker so job logs are traceable by story id."""
    correlation_id.set(value)
