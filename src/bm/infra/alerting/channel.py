"""Operational alert delivery (OD-19, NFR-OPS-003).

OD-19 is still open, and the sentence that governs this module is in it:

> a **named** alert recipient before go-live — an alert that reaches nobody is not
> monitoring.

So the one thing this code will not do is supply a recipient. There is no default
address, no fallback inbox, no "alerts@" guess. With alerting enabled and nothing
configured, `describe()` reports `unconfigured`, `/readyz` surfaces it, and every alert
is logged at `ERROR` with an explicit note that it was not delivered. The gap stays
visible until a human closes it.

Three further decisions:

* **Delivery never raises into the caller.** An alert is a side effect of something that
  already happened; a failed webhook must not fail the sale, the import or the nightly
  reconciliation that triggered it. Failures are logged and counted, never propagated.
* **Delivery happens outside the business transaction.** Alerts are raised from the
  outbox dispatcher (`ARCHITECTURE.md §7.1`), which runs after commit, so an HTTP call
  can never hold a database lock open.
* **The payload carries no customer data.** An alert says "reconciliation found 3
  discrepancies", not which customer or how much — it travels to a third-party webhook,
  and `SECURITY.md §4` does not permit shop financials to leave by that route.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from dataclasses import dataclass
from enum import StrEnum

from bm.config import Settings

logger = logging.getLogger(__name__)


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"

    @property
    def rank(self) -> int:
        return {"info": 0, "warning": 1, "critical": 2}[self.value]


class ChannelState(StrEnum):
    """What the operator needs to know about alerting at a glance."""

    #: Deliberately off. A local or test environment.
    DISABLED = "disabled"
    #: On, but nobody is going to receive anything. The OD-19 gap.
    UNCONFIGURED = "unconfigured"
    #: On, with a destination and a named human.
    CONFIGURED = "configured"
    #: On, with a destination but no named human. OD-19 asks for a person, not an
    #: endpoint — a webhook into an unwatched channel is the failure mode it names.
    NO_NAMED_RECIPIENT = "no_named_recipient"


@dataclass(frozen=True, slots=True)
class Alert:
    title: str
    body: str
    severity: Severity
    #: Stable identifier for the condition, so a receiver can group or deduplicate.
    kind: str


@dataclass(frozen=True, slots=True)
class ChannelStatus:
    state: ChannelState
    recipient_name: str | None
    #: The destination's *scheme and host only*. The full URL is a credential for most
    #: webhook providers — the path is the secret — so it is never reported or logged.
    destination_hint: str | None
    min_severity: str


class AlertChannel:
    """Delivers alerts, or explains clearly why it cannot."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._min = Severity(settings.alert_min_severity)

    def describe(self) -> ChannelStatus:
        url = (
            self._settings.alert_webhook_url.get_secret_value()
            if self._settings.alert_webhook_url
            else None
        )
        name = (self._settings.alert_recipient_name or "").strip() or None

        if not self._settings.alerting_enabled:
            state = ChannelState.DISABLED
        elif not url:
            state = ChannelState.UNCONFIGURED
        elif not name:
            state = ChannelState.NO_NAMED_RECIPIENT
        else:
            state = ChannelState.CONFIGURED

        return ChannelStatus(
            state=state,
            recipient_name=name,
            destination_hint=_scheme_and_host(url),
            min_severity=self._min.value,
        )

    def send(self, alert: Alert) -> bool:
        """Deliver one alert. Returns whether it left the building.

        `False` is a normal outcome, not an exception: alerting disabled, severity below
        the threshold, no recipient configured, or the endpoint refused. Each is logged
        at a level that matches how much the operator should care.
        """
        status = self.describe()

        if status.state is ChannelState.DISABLED:
            logger.info(
                "alert_not_sent_disabled",
                extra={"alert_kind": alert.kind, "severity": alert.severity.value},
            )
            return False

        if alert.severity.rank < self._min.rank:
            logger.info(
                "alert_below_threshold",
                extra={
                    "alert_kind": alert.kind,
                    "severity": alert.severity.value,
                    "threshold": self._min.value,
                },
            )
            return False

        if status.state is ChannelState.UNCONFIGURED:
            # ERROR, not WARNING: alerting is switched on and the alert is being
            # dropped. That is a monitoring outage, and OD-19 names it as one.
            logger.error(
                "alert_undeliverable_no_recipient",
                extra={
                    "alert_kind": alert.kind,
                    "severity": alert.severity.value,
                    "title": alert.title,
                    "remedy": "set BM_ALERT_WEBHOOK_URL (OD-19 is unresolved)",
                },
            )
            return False

        assert self._settings.alert_webhook_url is not None  # UNCONFIGURED covers None
        url = self._settings.alert_webhook_url.get_secret_value()
        payload = {
            "kind": alert.kind,
            "severity": alert.severity.value,
            "title": alert.title,
            "body": alert.body,
            "environment": self._settings.env,
            "release": self._settings.release,
        }

        try:
            request = urllib.request.Request(
                url,
                data=json.dumps(payload).encode("utf-8"),
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            if request.type not in ("https", "http"):
                raise ValueError(f"unsupported alert scheme {request.type!r}")
            if request.type == "http" and self._settings.env == "production":
                # An alert body names the shop and its environment. Plain HTTP would put
                # that on the wire in the one environment where it matters.
                raise ValueError("a production alert webhook must be https")
            with urllib.request.urlopen(
                request, timeout=self._settings.alert_timeout_seconds
            ) as response:
                ok = 200 <= response.status < 300
        except (urllib.error.URLError, ValueError, OSError) as exc:
            # Never propagates: see the module docstring. The thing that triggered the
            # alert already happened and is already recorded.
            logger.error(
                "alert_delivery_failed",
                extra={
                    "alert_kind": alert.kind,
                    "severity": alert.severity.value,
                    "error": str(exc)[:500],
                },
            )
            return False

        if not ok:
            logger.error(
                "alert_delivery_rejected",
                extra={"alert_kind": alert.kind, "status": response.status},
            )
            return False

        logger.info(
            "alert_sent",
            extra={
                "alert_kind": alert.kind,
                "severity": alert.severity.value,
                "recipient": status.recipient_name or "unnamed",
            },
        )
        return True


def _scheme_and_host(url: str | None) -> str | None:
    """`https://hooks.example.com/…` — never the path.

    For every common webhook provider the path *is* the credential, so reporting the
    full URL in a readiness probe would publish a secret to anything that can reach
    `/readyz`.
    """
    if not url:
        return None
    from urllib.parse import urlsplit

    parts = urlsplit(url)
    if not parts.scheme or not parts.netloc:
        return "malformed"
    return f"{parts.scheme}://{parts.netloc}/…"
