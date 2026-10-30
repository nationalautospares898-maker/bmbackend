"""Small helpers over SQLAlchemy result objects."""

from __future__ import annotations

from typing import Any, cast

from sqlalchemy import CursorResult
from sqlalchemy.engine import Result


def rows_affected(result: Result[Any]) -> int:
    """Rows a DML statement touched.

    `Session.execute` is typed as returning `Result`, which has no `rowcount`, but a
    DML statement always yields a `CursorResult`. The cast is contained here rather
    than repeated at every conditional-update call site — and those call sites matter:
    a zero rowcount is how optimistic concurrency and "already revoked" are detected,
    so the value is load-bearing, not diagnostic.
    """
    return int(cast(CursorResult[Any], result).rowcount or 0)
