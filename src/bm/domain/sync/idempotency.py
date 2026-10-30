"""Idempotency claiming and canonical fingerprinting (`P2-D-06`, `P2-D-07`).

Implements `Blueprint §5.2` exactly. **No exactly-once delivery is claimed** — across a
network delivery is at-least-once and some outcomes are genuinely unknown to the client.
What this guarantees is **no duplicate business effect**: a repeated request either does
the work once or returns the outcome of the work already done.

Two properties are load-bearing:

1. **The key row is the first write of the transaction, before any inventory lock**
   (`§5.2.1`). With the other order, a double-click blocks a genuine sale of the same
   product for up to `lock_timeout`; with this order it fails in microseconds holding no
   business locks.
2. **Canonicalisation re-renders money at the field's declared scale** (`§5.2.3` rule
   3). `"1250.0"` and `"1250.00"` are the same intent, and a client that reserialises
   differently between attempts would otherwise break its own retry in the one path
   designed to make a lost response safe.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from typing import Any

from sqlalchemy import select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from bm.domain.shared.errors import IdempotencyKeyReuse, IdempotentRequestInProgress
from bm.infra.db.enums import IdempotencyStatus
from bm.infra.db.models.infra import IdempotencyKey

#: Excluded from the fingerprint (`§5.2.4`).
#:
#: `draft_version` is excluded because a retry after the server bumped the version is
#: the *same* sale intent — including it would turn a legitimate retry into
#: `IDEMPOTENCY_KEY_REUSE`. Staleness is still enforced separately and still returns
#: `409 DRAFT_VERSION_CONFLICT`.
EXCLUDED_FIELDS: frozenset[str] = frozenset(
    {
        "draft_version",
        "client_created_at",
        "client_updated_at",
        "client_received_at",
    }
)

#: Declared scales from `DATA-MODEL.md §1`. Independent of the money policy: OD-7
#: governs *rounding*, not field width, so canonicalisation assumes no unapproved value.
FIELD_SCALES: dict[str, int] = {
    "amount": 2,
    "discount_amount": 2,
    "client_computed_total": 2,
    "total": 2,
    "unit_price": 4,
    "catalog_price_at_add": 4,
    "quantity": 3,
    "discount_percent": 3,
    "percent": 3,
}


@dataclass(frozen=True, slots=True)
class IdempotencyClaim:
    """The outcome of attempting to claim a key."""

    key: str
    fingerprint: str
    replayed: bool
    stored_status: int | None = None
    stored_body: Any | None = None
    row_id: uuid.UUID | None = None


def _canonical(value: Any, *, field_name: str | None = None) -> Any:
    """Apply the canonicalisation rules of `§5.2.3` recursively."""
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        # Rule 1: drop every key whose value is JSON null — an omitted field and an
        # explicit null are the same intent. Rule 9: drop the excluded fields.
        for key in sorted(value):
            if key in EXCLUDED_FIELDS:
                continue
            inner = value[key]
            if inner is None:
                continue
            out[key] = _canonical(inner, field_name=key)
        return out

    if isinstance(value, (list, tuple)):
        # Rule 4: array order is preserved. Line order is significant — `[A, B]` and
        # `[B, A]` are different bills.
        return [_canonical(v, field_name=field_name) for v in value]

    if isinstance(value, bool):
        # Rule 8: booleans and integers compare by value, not spelling (`1` != `true`).
        return value

    if isinstance(value, str):
        scale = FIELD_SCALES.get(field_name or "")
        if scale is not None:
            # Rule 3, the load-bearing one: normalise to a Decimal, then re-render at
            # the declared scale. No rounding — excess precision was already rejected
            # at validation with a 422 (`§5.2.3`).
            try:
                return format(Decimal(value), f".{scale}f")
            except (InvalidOperation, ValueError):
                return value
        if _looks_like_uuid(value):
            return value.lower()  # rule 5
        return value

    if isinstance(value, Decimal):
        scale = FIELD_SCALES.get(field_name or "", 2)
        return format(value, f".{scale}f")

    return value


def _looks_like_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def fingerprint(body: Any) -> str:
    """SHA-256 of the canonicalised body (`§5.2.3` rule 10)."""
    canonical = _canonical(body)
    serialised = json.dumps(canonical, separators=(",", ":"), ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(serialised.encode("utf-8")).hexdigest()


class IdempotencyService:
    def __init__(self, session: Session) -> None:
        self._session = session

    def claim(
        self,
        *,
        business_id: uuid.UUID,
        endpoint: str,
        key: uuid.UUID,
        body: Any,
        user_id: uuid.UUID | None = None,
        device_id: uuid.UUID | None = None,
    ) -> IdempotencyClaim:
        """Claim the key, or report the replay case.

        Must be the **first write** of the business transaction and must be called
        **before** any inventory lock is taken (`§5.2.1`).

        Behaviour (`§5.2.2`):

        | Situation | Result |
        |---|---|
        | New key | claimed, `replayed=False` |
        | Same key, same fingerprint, `completed` | the stored response verbatim |
        | Same key, same fingerprint, `in_progress` | `409 IDEMPOTENT_REQUEST_IN_PROGRESS` |
        | Same key, different fingerprint | `409 IDEMPOTENCY_KEY_REUSE` |
        """
        digest = fingerprint(body)
        key_text = str(key)
        row_id = uuid.uuid4()

        try:
            # A savepoint, so losing the unique-constraint race does not poison the
            # outer transaction — the loser still has to read the winner's row.
            with self._session.begin_nested():
                self._session.execute(
                    text(
                        "INSERT INTO idempotency_keys"
                        " (id, business_id, endpoint, key, request_fingerprint, status,"
                        "  user_id, device_id)"
                        " VALUES (:id, :business_id, :endpoint, :key, :fingerprint,"
                        "         CAST(:status AS idempotency_status), :user_id, :device_id)"
                    ),
                    {
                        "id": row_id,
                        "business_id": business_id,
                        "endpoint": endpoint,
                        "key": key_text,
                        "fingerprint": digest,
                        "status": str(IdempotencyStatus.IN_PROGRESS),
                        "user_id": user_id,
                        "device_id": device_id,
                    },
                )
        except IntegrityError:
            return self._resolve_existing(
                business_id=business_id, endpoint=endpoint, key=key_text, digest=digest
            )

        return IdempotencyClaim(key=key_text, fingerprint=digest, replayed=False, row_id=row_id)

    def _resolve_existing(
        self, *, business_id: uuid.UUID, endpoint: str, key: str, digest: str
    ) -> IdempotencyClaim:
        """Re-read the row and decide between the three replay cases.

        The loser of the insert race must **not** hold inventory locks while doing this
        (`§5.2.2`), which is why claiming happens before any lock is taken.
        """
        row = self._session.execute(
            select(
                IdempotencyKey.id,
                IdempotencyKey.request_fingerprint,
                IdempotencyKey.status,
                IdempotencyKey.response_status,
                IdempotencyKey.response_body,
            ).where(
                IdempotencyKey.business_id == business_id,
                IdempotencyKey.endpoint == endpoint,
                IdempotencyKey.key == key,
            )
        ).first()

        if row is None:  # pragma: no cover - the unique violation guarantees a row
            raise IdempotentRequestInProgress()

        row_id, stored_fingerprint, status, response_status, response_body = row

        if stored_fingerprint != digest:
            # Never silently accepted: the same key with a different body means the
            # client changed the bill without minting a new key, which is a client bug.
            raise IdempotencyKeyReuse(
                details={
                    "conflicting_fingerprint": stored_fingerprint,
                    "presented_fingerprint": digest,
                }
            )

        if str(status) == str(IdempotencyStatus.COMPLETED):
            return IdempotencyClaim(
                key=key,
                fingerprint=digest,
                replayed=True,
                stored_status=response_status,
                stored_body=response_body,
                row_id=row_id,
            )

        # Still in progress elsewhere. Retryable, with a backoff hint.
        raise IdempotentRequestInProgress()

    def store_response(
        self,
        claim: IdempotencyClaim,
        *,
        status_code: int,
        body: Any,
        resource_type: str | None = None,
        resource_id: uuid.UUID | None = None,
    ) -> None:
        """Record the outcome in the **same transaction** that commits the business
        effect (`§5.2.8` rule 3), so a stored response can never describe work that did
        not commit.

        Only committed outcomes are stored. A business rejection rolls the transaction
        back, leaving the key unconsumed and reusable once the user fixes the problem —
        which is correct, because it is still the same sale intent (`§5.2.5`).
        """
        if claim.row_id is None:
            return
        self._session.execute(
            update(IdempotencyKey)
            .where(IdempotencyKey.id == claim.row_id)
            .values(
                status=IdempotencyStatus.COMPLETED,
                response_status=status_code,
                response_body=body,
                resource_type=resource_type,
                resource_id=resource_id,
                completed_at=text("now()"),
            )
        )

    def find_invoice_by_key(self, *, business_id: uuid.UUID, key: str) -> uuid.UUID | None:
        """T3-01. Exact lookup so §5.4.5 case-12 arbitration is exact, not heuristic.

        Restricted to `reports.sales.read` at the route, and the key is **never**
        exposed in `print_payload`.
        """
        from bm.infra.db.models.sales import Invoice

        return self._session.execute(
            select(Invoice.id).where(
                Invoice.business_id == business_id, Invoice.idempotency_key == key
            )
        ).scalar_one_or_none()

    def prune(self, *, retention_days: int) -> int:
        """Delete keys past their retention window.

        Safe because the window (7 days) outlives the client's automatic retry window
        (60 minutes) by ~168× (`§5.2.6`), so pruning can never strand an automatic
        retry. The only path to a pruned key is a client left closed for over a week,
        handled as §5.4 case 12 — human arbitration.
        """
        from bm.infra.db.rows import rows_affected

        return rows_affected(
            self._session.execute(
                text(
                    "DELETE FROM idempotency_keys"
                    " WHERE created_at < now() - make_interval(days => :days)"
                ),
                {"days": retention_days},
            )
        )
