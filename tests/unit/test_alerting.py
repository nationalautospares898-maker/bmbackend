"""Operational alerting (OD-19, NFR-OPS-003).

OD-19 is unresolved and says plainly that "an alert that reaches nobody is not
monitoring". So the behaviour under test is mostly about **honesty when unconfigured**:
no invented recipient, no silent drop, and a state an operator can read.

The delivery tests use a real loopback HTTP server rather than a patched `urlopen`,
because what is being asserted includes the timeout, the scheme check and the handling
of a non-2xx reply — all of which a mock would simply agree with.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest
from pydantic import SecretStr

from bm.config import Settings
from bm.infra.alerting.channel import Alert, AlertChannel, ChannelState, Severity

TEST_SECRET = "x" * 32


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "env": "test",
        "database_url": "postgresql+psycopg://u:p@localhost:55439/bm",
        "jwt_signing_key": SecretStr(TEST_SECRET),
        "password_pepper": SecretStr(TEST_SECRET),
        "ip_hash_pepper": SecretStr(TEST_SECRET),
    }
    base.update(overrides)
    return Settings(**base)


def _alert(severity: Severity = Severity.CRITICAL) -> Alert:
    return Alert(
        title="NAS: stock reconciliation discrepancy",
        body="2 stock balance(s) disagree with the movement ledger",
        severity=severity,
        kind="inventory.reconcile",
    )


# --- the OD-19 gap, made visible ----------------------------------------------


def test_alerting_is_off_by_default_and_says_so() -> None:
    """Off is an honest state. A local or test environment has no pager."""
    assert AlertChannel(_settings()).describe().state is ChannelState.DISABLED


def test_enabled_with_no_destination_reports_unconfigured() -> None:
    """The OD-19 gap. Not "configured with a default" — there is no default."""
    status = AlertChannel(_settings(alerting_enabled=True)).describe()
    assert status.state is ChannelState.UNCONFIGURED
    assert status.recipient_name is None
    assert status.destination_hint is None


def test_a_destination_with_no_named_person_is_its_own_state() -> None:
    """OD-19 asks for a *named* recipient, not an endpoint. A webhook into an unwatched
    channel is the exact failure it describes, so it does not report as `configured`."""
    status = AlertChannel(
        _settings(
            alerting_enabled=True,
            alert_webhook_url=SecretStr("https://hooks.example.com/services/T000/B000/xyz"),
        )
    ).describe()
    assert status.state is ChannelState.NO_NAMED_RECIPIENT


def test_a_destination_and_a_named_person_is_configured() -> None:
    status = AlertChannel(
        _settings(
            alerting_enabled=True,
            alert_webhook_url=SecretStr("https://hooks.example.com/services/T000/B000/xyz"),
            alert_recipient_name="Upendra Gajam",
        )
    ).describe()
    assert status.state is ChannelState.CONFIGURED
    assert status.recipient_name == "Upendra Gajam"


def test_a_whitespace_only_recipient_name_does_not_count_as_named() -> None:
    status = AlertChannel(
        _settings(
            alerting_enabled=True,
            alert_webhook_url=SecretStr("https://hooks.example.com/x/y"),
            alert_recipient_name="   ",
        )
    ).describe()
    assert status.state is ChannelState.NO_NAMED_RECIPIENT


def test_an_unconfigured_channel_refuses_to_claim_delivery() -> None:
    """The important half: it returns `False`. A channel that returned `True` would
    make the dispatcher mark the event dispatched and lose it."""
    assert AlertChannel(_settings(alerting_enabled=True)).send(_alert()) is False


def test_a_disabled_channel_also_refuses_to_claim_delivery() -> None:
    assert AlertChannel(_settings()).send(_alert()) is False


# --- the destination hint is not a credential ---------------------------------


def test_the_destination_hint_carries_the_host_but_never_the_path() -> None:
    """For every common webhook provider the path *is* the secret, so reporting the
    full URL in a readiness probe would publish it to anything that can reach
    `/readyz`."""
    secret_path = "/services/T00000000/B00000000/aVeryRealLookingSecret"
    status = AlertChannel(
        _settings(
            alerting_enabled=True,
            alert_webhook_url=SecretStr(f"https://hooks.example.com{secret_path}"),
            alert_recipient_name="Owner",
        )
    ).describe()
    assert status.destination_hint == "https://hooks.example.com/…"
    assert "aVeryRealLookingSecret" not in (status.destination_hint or "")


def test_a_malformed_url_is_reported_as_malformed_rather_than_echoed() -> None:
    status = AlertChannel(
        _settings(alerting_enabled=True, alert_webhook_url=SecretStr("not-a-url"))
    ).describe()
    assert status.destination_hint == "malformed"


# --- severity threshold -------------------------------------------------------


def test_an_alert_below_the_threshold_is_not_sent() -> None:
    channel = AlertChannel(
        _settings(
            alerting_enabled=True,
            alert_min_severity="critical",
            alert_webhook_url=SecretStr("https://hooks.example.com/x"),
            alert_recipient_name="Owner",
        )
    )
    assert channel.send(_alert(Severity.WARNING)) is False


def test_severity_ranks_order_correctly() -> None:
    assert Severity.INFO.rank < Severity.WARNING.rank < Severity.CRITICAL.rank


# --- scheme safety ------------------------------------------------------------


def test_a_non_http_scheme_is_refused_rather_than_attempted() -> None:
    channel = AlertChannel(
        _settings(
            alerting_enabled=True,
            alert_webhook_url=SecretStr("file:///etc/passwd"),
            alert_recipient_name="Owner",
        )
    )
    assert channel.send(_alert()) is False


def test_a_production_webhook_must_be_https() -> None:
    """An alert body names the shop and its environment. Plain HTTP would put that on
    the wire in the one environment where it matters."""
    channel = AlertChannel(
        _settings(
            env="production",
            alerting_enabled=True,
            alert_webhook_url=SecretStr("http://hooks.example.com/x"),
            alert_recipient_name="Owner",
        )
    )
    assert channel.send(_alert()) is False


# --- real delivery ------------------------------------------------------------


class _Recorder(BaseHTTPRequestHandler):
    received: list[dict[str, Any]] = []
    reply_status = 200

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).received.append(json.loads(body))
        self.send_response(type(self).reply_status)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *_args: Any) -> None:
        """Silence the default stderr access log."""


@pytest.fixture
def webhook() -> Any:
    _Recorder.received = []
    _Recorder.reply_status = 200
    server = HTTPServer(("127.0.0.1", 0), _Recorder)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server
    server.shutdown()
    server.server_close()


def _channel_for(server: Any, **overrides: Any) -> AlertChannel:
    host, port = server.server_address[0], server.server_address[1]
    return AlertChannel(
        _settings(
            alerting_enabled=True,
            alert_webhook_url=SecretStr(f"http://{host}:{port}/hook"),
            alert_recipient_name="Owner",
            alert_timeout_seconds=5.0,
            **overrides,
        )
    )


def test_a_configured_channel_delivers_the_alert(webhook: Any) -> None:
    assert _channel_for(webhook).send(_alert()) is True
    assert len(_Recorder.received) == 1
    assert _Recorder.received[0]["kind"] == "inventory.reconcile"
    assert _Recorder.received[0]["severity"] == "critical"


def test_the_payload_carries_the_environment_so_a_staging_alert_is_identifiable(
    webhook: Any,
) -> None:
    _channel_for(webhook, release="1.2.3").send(_alert())
    assert _Recorder.received[0]["environment"] == "test"
    assert _Recorder.received[0]["release"] == "1.2.3"


def test_a_rejected_delivery_is_not_claimed_as_sent(webhook: Any) -> None:
    _Recorder.reply_status = 500
    assert _channel_for(webhook).send(_alert()) is False


def test_an_unreachable_endpoint_never_raises_into_the_caller() -> None:
    """The thing that triggered the alert already happened and is already recorded. A
    failed webhook must not fail the reconciliation that raised it."""
    channel = AlertChannel(
        _settings(
            alerting_enabled=True,
            # Port 1 is reserved and nothing listens there.
            alert_webhook_url=SecretStr("http://127.0.0.1:1/hook"),
            alert_recipient_name="Owner",
            alert_timeout_seconds=1.0,
        )
    )
    assert channel.send(_alert()) is False
