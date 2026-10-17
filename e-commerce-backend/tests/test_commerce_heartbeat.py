"""
Covers analytics/reporting.py::post_heartbeat and the
report_commerce_heartbeat management command and Celery task -- the POST's
auth header and body shape (mocked, never a real network call), that a
missing GENMARS_SYSTEM_KEY fails cleanly, and that the key is never logged.
Mirrors tests/test_commerce_metrics_report.py::TestReportingErrors, which
covers the same shapes of failure for post_metrics.
"""

import io
import json
import urllib.error
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from analytics.reporting import ReportingError, post_heartbeat


@pytest.mark.django_db
class TestHeartbeat:
    def test_missing_system_key_raises_a_safe_reporting_error(self, settings):
        settings.GENMARS_SYSTEM_KEY = ""

        with pytest.raises(ReportingError, match="GENMARS_SYSTEM_KEY"):
            post_heartbeat()

    def test_the_command_fails_cleanly_without_a_system_key(self, settings):
        settings.GENMARS_SYSTEM_KEY = ""

        with pytest.raises(CommandError):
            call_command("report_commerce_heartbeat")

    @patch("analytics.reporting.urllib.request.urlopen")
    def test_posts_the_right_auth_header_and_url(self, mock_urlopen, settings):
        settings.GENMARS_SYSTEM_KEY = "test-system-key"
        settings.GENMARS_API_ORIGIN = "https://api.genmars.test"
        mock_urlopen.return_value.__enter__.return_value.status = 200

        call_command("report_commerce_heartbeat")

        assert mock_urlopen.called
        request = mock_urlopen.call_args[0][0]
        assert request.full_url == "https://api.genmars.test/api/systems/heartbeat"
        assert request.get_header("Authorization") == "Bearer test-system-key"
        assert request.get_header("Content-type") == "application/json"
        assert json.loads(request.data.decode()) == {}

    @patch("analytics.reporting.urllib.request.urlopen")
    def test_an_optional_version_is_carried_in_the_body(self, mock_urlopen, settings):
        settings.GENMARS_SYSTEM_KEY = "test-system-key"
        mock_urlopen.return_value.__enter__.return_value.status = 200

        post_heartbeat(version="2026.10.17")

        request = mock_urlopen.call_args[0][0]
        assert json.loads(request.data.decode()) == {"version": "2026.10.17"}

    @patch("analytics.reporting.urllib.request.urlopen")
    def test_a_rejected_heartbeat_fails_the_command_without_leaking_the_key(
        self, mock_urlopen, settings, caplog
    ):
        settings.GENMARS_SYSTEM_KEY = "super-secret-key"
        mock_urlopen.side_effect = urllib.error.HTTPError(
            "https://api.genmars.test/api/systems/heartbeat",
            401,
            "unauthorized",
            {},
            io.BytesIO(b"unauthorized"),
        )

        with pytest.raises(CommandError):
            call_command("report_commerce_heartbeat")

        assert "super-secret-key" not in caplog.text

    @patch("analytics.reporting.urllib.request.urlopen")
    def test_a_network_error_fails_the_command_cleanly(self, mock_urlopen, settings):
        settings.GENMARS_SYSTEM_KEY = "test-system-key"
        mock_urlopen.side_effect = urllib.error.URLError("no route to host")

        with pytest.raises(CommandError):
            call_command("report_commerce_heartbeat")


@pytest.mark.django_db
def test_the_celery_task_never_raises_when_the_key_is_missing(settings):
    from analytics.tasks import report_commerce_heartbeat

    settings.GENMARS_SYSTEM_KEY = ""

    report_commerce_heartbeat()  # must not raise
