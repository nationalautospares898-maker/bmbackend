"""Time-ordered identifiers for append-only ledger rows.

`stock_movements.created_at` defaults to `now()`, which in PostgreSQL is the
**transaction** timestamp — identical for every row one transaction writes. The fifteen
opening rows of the demo tenant all carry `2026-10-31 17:46:24.866975+00`, and a sale
that lists the same part twice writes two rows with one timestamp.

That makes `ORDER BY created_at` an incomplete order, and a random `uuid4` tiebreaker no
order at all. A product's history is read **downwards** — each row's `balance_after`
should follow from the one above it — so an arbitrary tie makes the running balance column
look wrong even though every individual row is right.

UUIDv7 fixes it at the source: the first 48 bits are a Unix-millisecond timestamp, so
`(created_at, id)` sorts by actual insertion order, including inside a single
transaction. The frontend already generates its ids this way (`src/lib/ids.ts`) and for
the same reason.

The clock here is only ever an **ordering prefix**. No business date is derived from it —
that comes from `shared/clock.py` and the business timezone.
"""

from __future__ import annotations

import os
import time
import uuid


def uuid7() -> uuid.UUID:
    """A time-ordered UUID: 48 bits of Unix milliseconds, then 74 bits of randomness.

    Monotonic between calls within the same millisecond is **not** guaranteed, and is not
    needed: two rows written in the same millisecond are already ordered by the random
    tail consistently for any reader, which is what the history column requires. What
    matters is that a row written later never sorts before one written earlier by more
    than a millisecond.
    """
    millis = time.time_ns() // 1_000_000
    payload = bytearray(os.urandom(16))
    payload[0:6] = millis.to_bytes(6, "big")
    payload[6] = (payload[6] & 0x0F) | 0x70  # version 7
    payload[8] = (payload[8] & 0x3F) | 0x80  # RFC 4122 variant
    return uuid.UUID(bytes=bytes(payload))
