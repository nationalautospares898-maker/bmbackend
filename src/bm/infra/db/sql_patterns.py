"""SQL `LIKE` pattern helpers.

Lives in `infra` because escaping is a SQL-dialect concern, not a business rule — and
because both the domain services (which build patterns) and the repositories (which run
them) need the **same** implementation. Two implementations that disagree by one
character mean a product that can be created but never found.
"""

from __future__ import annotations

#: `%` and `_` are `LIKE` wildcards; `\` is the escape character itself.
_LIKE_SPECIALS = str.maketrans({"%": r"\%", "_": r"\_", "\\": "\\\\"})


def escape_like(value: str) -> str:
    """Escape `LIKE` wildcards in user input.

    Without this, a query of `%` matches every row and turns autocomplete into a full
    scan of a 100k-product catalog (`SECURITY.md §4.1`).
    """
    return value.translate(_LIKE_SPECIALS)


def prefix(value: str) -> str:
    """A prefix pattern: `abc` -> `abc%`."""
    return f"{escape_like(value)}%"


def contains(value: str) -> str:
    """A contains pattern: `abc` -> `%abc%`."""
    return f"%{escape_like(value)}%"
