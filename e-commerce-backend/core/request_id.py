"""
Request correlation. Every log line emitted while handling a request is
tagged with the same id (core/logging.py's filter reads it from here), so a
problem reported against one request can be traced across every log
statement it produced — without this, a business-scoped incident in a
shared log stream (many businesses, one process) has no way to be told
apart from any other request's lines.

A `contextvars.ContextVar`, not a thread-local: Django's own request
handling is synchronous-per-thread today, but a ContextVar is also correct
if any of this ever runs under ASGI/async views, where a thread-local would
silently leak state across concurrent requests sharing a thread.
"""

import re
import uuid
from contextvars import ContextVar

_request_id: ContextVar[str] = ContextVar("request_id", default="-")

# A client- or proxy-supplied id is trusted for correlation, never for
# anything security-sensitive — but it does get echoed into a response
# header and into every log line for the request, so it's shaped first
# rather than accepted as an arbitrary string. Anything that doesn't look
# like a plausible id (not this shape, or absurdly long) is replaced with a
# generated one rather than rejected outright — a malformed header should
# never turn into a failed request.
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def get_request_id() -> str:
    return _request_id.get()


class RequestIDMiddleware:
    """
    Reads `X-Request-ID` from an upstream proxy/load balancer if present —
    so a request can be traced across process boundaries, not just within
    this one — generates a fresh id otherwise, and echoes it back on the
    response so whatever sits in front of this app can correlate its own
    logs against this app's.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        supplied = request.headers.get("X-Request-ID", "")
        request_id = supplied if _VALID_REQUEST_ID.match(supplied) else uuid.uuid4().hex
        request.request_id = request_id
        token = _request_id.set(request_id)
        try:
            response = self.get_response(request)
        finally:
            _request_id.reset(token)
        response["X-Request-ID"] = request_id
        return response
