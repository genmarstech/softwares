"""
Structured (JSON) logging — stdlib `logging` + `json` only, no new
dependency (Charter 03 §I). Each line is one JSON object: timestamp,
level, logger name, message, and the request id from core/request_id.py,
so log lines from a shared process serving many businesses can be
correlated back to one request rather than only to "this process, roughly
this time."

Never put anything secret-shaped into a log record's `message` — this
formatter doesn't redact, it formats. payments/mpesa.py's own rule (never
log the consumer secret, passkey, or generated password) and
accounts/mail_backends.py's (never log message bodies) are enforced at the
call site, not here; this file has no way to know which fields in an
arbitrary message string are sensitive.
"""

import json
import logging

from .request_id import get_request_id


class RequestIDLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        record.request_id = get_request_id()
        return True


class JSONFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": getattr(record, "request_id", "-"),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload)
