"""Errors that carry a stable machine code beside their English prose.

Why not `Accept-Language`. The single string most worth translating is the one
written when a story fails, and that is written by the ARQ worker: a separate
process, started from a job id, with no request and no headers anywhere in
scope. Threading request context through the queue to reach it would be a large
change that still leaves every already-failed row in English. A code travels
fine through a database column; a header does not.

The envelope is additive on purpose. `detail` stays a string forever — the
client reads it with `typeof detail === 'string'`, and turning it into an object
would degrade every error in the product to "Request failed (429)". `code` and
`params` are siblings, so an older client ignores them and a newer one prefers
them. English readers keep seeing the server's own prose; it is the source of
truth and is never duplicated into the frontend catalogue.
"""

from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

# Values are interpolated into a sentence a parent reads. Restricting them to
# scalars is what stops an exception string or an internal id from being handed
# to a caller as a "parameter" — the same separation services.base.GenerationError
# already draws between its log message and its user_message.
_ALLOWED_PARAM_TYPES = (str, int, float, bool)


def clean_params(params: dict[str, Any] | None) -> dict[str, Any]:
    if not params:
        return {}
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if not isinstance(value, _ALLOWED_PARAM_TYPES):
            raise TypeError(f"Error param {key!r} must be a string or number, got {type(value).__name__}")
        cleaned[key] = value
    return cleaned


class CodedHTTPException(HTTPException):
    """An HTTPException that also names itself.

    `detail` is still the English sentence and is still what any client that
    has never heard of `code` will show.
    """

    def __init__(
        self,
        status_code: int,
        *,
        code: str,
        detail: str,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(status_code=status_code, detail=detail, headers=headers)
        self.code = code
        self.params = clean_params(params)


async def coded_exception_handler(request: Request, exc: CodedHTTPException) -> JSONResponse:
    content: dict[str, Any] = {"detail": exc.detail, "code": exc.code}
    if exc.params:
        content["params"] = exc.params
    return JSONResponse(status_code=exc.status_code, content=content, headers=exc.headers)


# ---------------------------------------------------------------------------
# Codes written into Story.error_code / GenerationJob.error_code.
#
# These outlive a request: they are frozen into rows that a parent may open
# months later, so renaming one silently changes what an old row renders as.
# Add codes; do not repurpose them.
GENERATION_FAILED = "generation.failed"
GENERATION_INTERRUPTED = "generation.interrupted"
GENERATION_STALLED = "generation.stalled"
# The story model refused the idea. Distinct from the generic failure because
# the advice differs: this one is fixable by the parent, and the fix ("try a
# gentler idea") is the whole value of the message.
GENERATION_BLOCKED = "generation.blocked"
