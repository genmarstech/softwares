"""
/health/ — unauthenticated, minimal, and deliberately dumb: checks that the
database this process actually needs for every request is reachable, and
nothing else. No secrets, no config, no stack trace ever reaches the
response body — a real failure is logged server-side (tagged with this
request's id, like everything else) and the client gets a flat
{"status": "error"}, the same shape whatever actually broke.

Deliberately not checking Redis/Celery here: a broken broker stops
low-stock alerts and status emails, not the request/response cycle this
endpoint exists to represent — conflating the two would report the whole
app unhealthy over something that doesn't actually block a request.
"""

import logging

from django.db import connections
from django.http import JsonResponse
from django.views import View

log = logging.getLogger(__name__)


class HealthCheckView(View):
    def get(self, request):
        try:
            connections["default"].ensure_connection()
        except Exception:
            log.exception("health check failed: database unreachable")
            return JsonResponse({"status": "error"}, status=503)
        return JsonResponse({"status": "ok"})
