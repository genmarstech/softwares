"""
A single exception handler for every DRF view.

Checklist rule: "Return safe error messages without exposing stack traces or
secrets." DRF's default handler already keeps `ValidationError`/`Http404`/etc.
safe; what it does NOT do is catch an unexpected exception, which otherwise
propagates past DRF and — outside DEBUG — becomes Django's bare-HTML 500 page
instead of the JSON error shape every other endpoint returns. This file makes
that one case consistent too, and always logs the real exception server-side.
"""

import logging

from rest_framework.response import Response
from rest_framework.views import exception_handler
from rest_framework import status

log = logging.getLogger(__name__)


def safe_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        return response

    # Anything DRF didn't recognise (a bug, a third-party error) — log the
    # real thing, return a generic message. Never exc's own str() to the
    # client: it can carry a DB error with a table/column name, a file path,
    # or similar.
    log.exception("Unhandled exception in %s", context.get("view"))
    return Response(
        {"detail": "Something went wrong on our side. Please try again."},
        status=status.HTTP_500_INTERNAL_SERVER_ERROR,
    )
