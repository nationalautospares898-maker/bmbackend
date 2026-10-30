"""Normalisation rules for search.

These live in one place because the same rule has to be applied at write time (when
`normalized_*` and `search_text` are stored) and at read time (when a query is
normalised before matching). Two implementations that drift by one character mean a
product that can be created but never found.

The part-number rule is the load-bearing one. A counter hand will type `JK-123`,
`jk 123`, `jk123` or `JK.123` for the same part, and all four must match
(FR-CAT-006) — so separators are stripped entirely rather than normalised to one form.
"""

from __future__ import annotations

import re
import unicodedata

from bm.infra.db.sql_patterns import escape_like

#: Separators that carry no meaning in a part number.
_SKU_SEPARATORS = re.compile(r"[-_/.\\,:;()\[\]\s]+")
_WHITESPACE = re.compile(r"\s+")
_NON_DIGIT = re.compile(r"\D+")


def _strip_accents(value: str) -> str:
    """`Alliéd` and `Allied` are the same brand to someone searching for it.

    Done in Python rather than via the `unaccent` extension so the stored
    `normalized_*` value and a query normalised in the application agree exactly.
    """
    decomposed = unicodedata.normalize("NFKD", value)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def normalize_name(value: str) -> str:
    """Lower-case, accent-stripped, whitespace-collapsed."""
    return _WHITESPACE.sub(" ", _strip_accents(value).lower()).strip()


def normalize_sku(value: str | None) -> str | None:
    """Lower-case with every separator removed.

    Returns `None` for an empty result so the partial unique index on
    `products.normalized_sku` still permits many products without a part code.
    """
    if value is None:
        return None
    collapsed = _SKU_SEPARATORS.sub("", _strip_accents(value).lower())
    return collapsed or None


def normalize_phone(value: str | None) -> str | None:
    """Digits only, keeping the last ten.

    `+91 98765 43210`, `098765 43210` and `9876543210` are one phone number to the
    person who owns it, and the duplicate detector (FR-CUST-004) is useless if they
    hash differently. Keeping the last ten digits drops a country code or a trunk `0`
    without needing to know which is which.
    """
    if value is None:
        return None
    digits = _NON_DIGIT.sub("", value)
    if not digits:
        return None
    return digits[-10:] if len(digits) > 10 else digits


def normalize_vehicle_number(value: str | None) -> str | None:
    """Upper-case with separators stripped — `KA 01 AB 1234` → `KA01AB1234`.

    The **entered** form is stored separately and printed verbatim; this is only for
    lookup (`test_vehicle_normalisation_preserves_input`).
    """
    if value is None:
        return None
    collapsed = _SKU_SEPARATORS.sub("", _strip_accents(value).upper())
    return collapsed or None


def build_search_text(
    *, name: str, sku: str | None, brand: str | None, aliases: list[str] | None = None
) -> str:
    """The haystack for the trigram tier of product search.

    Both the readable and the separator-stripped forms of the part code are included, so
    a trigram match can find `jk-123` whether the user typed the separators or not.
    """
    parts: list[str] = [normalize_name(name)]
    if sku:
        parts.append(normalize_name(sku))
        stripped = normalize_sku(sku)
        if stripped and stripped != normalize_name(sku):
            parts.append(stripped)
    if brand:
        parts.append(normalize_name(brand))
    for alias in aliases or []:
        parts.append(normalize_name(alias))
        stripped_alias = normalize_sku(alias)
        if stripped_alias:
            parts.append(stripped_alias)
    # De-duplicated while preserving order, so the column stays small on a catalog with
    # many near-identical aliases.
    seen: set[str] = set()
    ordered: list[str] = []
    for part in parts:
        if part and part not in seen:
            seen.add(part)
            ordered.append(part)
    return " ".join(ordered)


#: Re-exported so callers have one obvious import for "normalise then match", while the
#: single implementation lives in `infra` where both layers can reach it.
__all__ = [
    "build_search_text",
    "escape_like",
    "normalize_name",
    "normalize_phone",
    "normalize_sku",
    "normalize_vehicle_number",
]
