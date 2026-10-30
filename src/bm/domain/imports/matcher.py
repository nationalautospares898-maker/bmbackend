"""Deciding what each parsed row *appears* to refer to (FR-IMP-006, FR-IMP-007).

The matcher never resolves anything. It classifies, and the classification decides
whether a human has to rule on the row before it can be applied. The rule that matters:

> Nothing auto-creates a product or moves stock from a fuzzy guess (FR-IMP-007).

So the four statuses `ambiguous`, `new_product`, `duplicate_in_file` and `invalid` are
not warnings — they are blocks, enforced in the database by
`ck_import_rows__decision_required` as well as here. A supplier's typo must not become a
new product in this shop's catalogue because a similarity score cleared 0.8.

`fuzzy_candidate` is the one borderline case, and it is deliberately *not* a block: a
single strong trigram match on the name, with no rival within reach, is treated the same
way the search box treats it — as a match the user can see and override. If there is
more than one plausible candidate it becomes `ambiguous` instead, because then the
software genuinely does not know.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.imports.parser import ParsedRow
from bm.domain.shared.normalize import normalize_name, normalize_sku
from bm.infra.db.enums import ImportIntent, ImportMatchStatus

#: Trigram similarity at or above this, with no rival within `_RIVAL_MARGIN`, is a
#: `fuzzy_candidate`. Below it the row is a `new_product` decision.
#:
#: 0.62 rather than a rounder number because it is the point at which the shop's own
#: part names stop colliding: `FILTER OIL ELEMENT` vs `FILTER OIL ELEMENT HD` sits just
#: under it, and those are two different parts.
FUZZY_ACCEPT = Decimal("0.62")

#: A second candidate this close to the best one means the software cannot choose.
_RIVAL_MARGIN = Decimal("0.08")

#: How many candidates to carry into the review screen.
_MAX_CANDIDATES = 5

#: Exact matches get a confidence of 1; it is stored so the review screen can sort and
#: so an audit can show why a row was treated as certain.
_CERTAIN = Decimal("1.0000")

_EXACT_SKU_SQL = text(
    """
    SELECT id
      FROM products
     WHERE business_id = :business_id
       AND normalized_sku = :sku
     LIMIT 2
    """
)

_EXACT_ALIAS_SQL = text(
    """
    SELECT DISTINCT a.product_id
      FROM product_aliases a
      JOIN products p ON p.id = a.product_id
     WHERE a.business_id = :business_id
       AND a.normalized_alias = :sku
       AND p.business_id = :business_id
     LIMIT 2
    """
)

_BARCODE_SQL = text(
    """
    SELECT id
      FROM products
     WHERE business_id = :business_id
       AND barcode = :barcode
     LIMIT 2
    """
)

#: Trigram candidates by name, served by `ix_products__name_trgm` (migration `0018`).
#:
#: The `%` is a single character, not `%%`. SQLAlchemy's `text()` does not treat `%` as
#: a format placeholder, so doubling it produces a literal `%%` — which is not an
#: operator, and the query raises. Worse, it raises *only* on the rows that reach the
#: fuzzy tier, since an exact part-code match returns before this runs — so a file whose
#: codes all matched looked perfectly healthy. `repositories/catalog.py` had it right.
_FUZZY_SQL = text(
    """
    SELECT id, similarity(normalized_name, :name) AS score
      FROM products
     WHERE business_id = :business_id
       AND normalized_name % :name
     ORDER BY score DESC, normalized_name
     LIMIT :limit
    """
)


@dataclass(frozen=True, slots=True)
class RowMatch:
    """How one row was classified, and what the review screen needs to show for it."""

    match_status: ImportMatchStatus
    matched_product_id: uuid.UUID | None = None
    candidate_product_ids: list[uuid.UUID] | None = None
    match_confidence: Decimal | None = None
    #: Why this row is `invalid`, or why a human has to choose. Shown verbatim.
    reason: str | None = None

    @property
    def needs_decision(self) -> bool:
        from bm.infra.db.enums import DECISION_REQUIRED_MATCH_STATUSES

        return self.match_status in DECISION_REQUIRED_MATCH_STATUSES


class RowMatcher:
    """Classifies parsed rows against the live catalogue.

    Constructed per job. Holds a small per-run cache keyed on the normalised part code,
    because supplier price lists repeat the same code across variants and re-running the
    trigram query for each one is the difference between a two-second parse and a
    thirty-second one on a 5 000-row file.
    """

    def __init__(self, session: Session, business_id: uuid.UUID, *, intent: ImportIntent) -> None:
        self._session = session
        self._business_id = business_id
        self._intent = intent
        self._cache: dict[tuple[str, str], RowMatch] = {}

    def classify(self, row: ParsedRow, *, seen_keys: dict[str, int]) -> RowMatch:
        """Classify one row.

        `seen_keys` maps an identity key → the earlier `row_index` that used it, and is
        mutated as rows are classified. A file that lists the same part twice is a
        `duplicate_in_file` on the *second* occurrence (FR-IMP-006): the first is a
        normal row, so a price list with an accidental repeat still imports its unique
        lines once the repeat is ruled on.
        """
        if row.error:
            return RowMatch(match_status=ImportMatchStatus.INVALID, reason=row.error)

        missing = self._missing_required_field(row)
        if missing is not None:
            return RowMatch(match_status=ImportMatchStatus.INVALID, reason=missing)

        identity = self._identity_key(row)
        if identity in seen_keys:
            return RowMatch(
                match_status=ImportMatchStatus.DUPLICATE_IN_FILE,
                reason=f"The same item appears earlier in this file, on row {seen_keys[identity]}.",
            )
        seen_keys[identity] = row.row_index

        return self._lookup(row)

    def _missing_required_field(self, row: ParsedRow) -> str | None:
        """Intent-specific requirements (FR-IMP-002).

        A price list without a price and a receipt without a quantity are both useless,
        and both are better refused in review than applied as a no-op the user believes
        worked.
        """
        if self._intent is ImportIntent.PRICE_LIST:
            if row.selling_price is None and row.unit_cost is None:
                return "A price list row needs a selling price or a cost."
        elif row.quantity is None:
            return "A supplier bill row needs a quantity."
        return None

    def _identity_key(self, row: ParsedRow) -> str:
        """What counts as "the same item" within one file.

        The part code when there is one, since that is what a supplier keys on; the
        normalised name otherwise. Prefixed so a code and a name can never collide.
        """
        sku = normalize_sku(row.sku) if row.sku else None
        if sku:
            return f"sku:{sku}"
        return f"name:{normalize_name(row.name or '')}"

    def _lookup(self, row: ParsedRow) -> RowMatch:
        sku = normalize_sku(row.sku) if row.sku else None
        name = normalize_name(row.name or "")
        cache_key = (sku or "", name)
        cached = self._cache.get(cache_key)
        if cached is not None:
            return cached
        found = self._lookup_uncached(row, sku=sku, name=name)
        self._cache[cache_key] = found
        return found

    def _lookup_uncached(self, row: ParsedRow, *, sku: str | None, name: str) -> RowMatch:
        if sku:
            ids = [
                r[0]
                for r in self._session.execute(
                    _EXACT_SKU_SQL, {"business_id": self._business_id, "sku": sku}
                ).all()
            ]
            if len(ids) == 1:
                return RowMatch(
                    match_status=ImportMatchStatus.EXACT_SKU,
                    matched_product_id=ids[0],
                    match_confidence=_CERTAIN,
                )
            if len(ids) > 1:  # pragma: no cover - uq_products__sku makes this impossible
                return RowMatch(
                    match_status=ImportMatchStatus.AMBIGUOUS,
                    candidate_product_ids=ids,
                    reason="More than one product carries this part code.",
                )

            alias_ids = [
                r[0]
                for r in self._session.execute(
                    _EXACT_ALIAS_SQL, {"business_id": self._business_id, "sku": sku}
                ).all()
            ]
            if len(alias_ids) == 1:
                return RowMatch(
                    match_status=ImportMatchStatus.EXACT_ALIAS,
                    matched_product_id=alias_ids[0],
                    match_confidence=_CERTAIN,
                )
            if len(alias_ids) > 1:
                return RowMatch(
                    match_status=ImportMatchStatus.AMBIGUOUS,
                    candidate_product_ids=alias_ids,
                    reason="This code is an alternate code for more than one product.",
                )

        if row.barcode:
            barcode_ids = [
                r[0]
                for r in self._session.execute(
                    _BARCODE_SQL, {"business_id": self._business_id, "barcode": row.barcode}
                ).all()
            ]
            if len(barcode_ids) == 1:
                return RowMatch(
                    match_status=ImportMatchStatus.EXACT_SKU,
                    matched_product_id=barcode_ids[0],
                    match_confidence=_CERTAIN,
                    reason="Matched on barcode.",
                )

        if not name:
            return RowMatch(
                match_status=ImportMatchStatus.NEW_PRODUCT,
                reason="This part code is not in the catalogue, and the row has no name "
                "to create one from.",
            )

        candidates = self._session.execute(
            _FUZZY_SQL,
            {"business_id": self._business_id, "name": name, "limit": _MAX_CANDIDATES},
        ).all()
        if not candidates:
            return RowMatch(
                match_status=ImportMatchStatus.NEW_PRODUCT,
                reason="Nothing in the catalogue looks like this.",
            )

        best_id, best_score_raw = candidates[0][0], candidates[0][1]
        best = Decimal(str(best_score_raw)).quantize(Decimal("0.0001"))
        rivals = [
            c[0]
            for c in candidates[1:]
            if Decimal(str(c[1])).quantize(Decimal("0.0001")) >= best - _RIVAL_MARGIN
        ]

        if best < FUZZY_ACCEPT:
            return RowMatch(
                match_status=ImportMatchStatus.NEW_PRODUCT,
                candidate_product_ids=[c[0] for c in candidates],
                match_confidence=best,
                reason="No close enough match; this would be a new product.",
            )
        if rivals:
            return RowMatch(
                match_status=ImportMatchStatus.AMBIGUOUS,
                candidate_product_ids=[best_id, *rivals],
                match_confidence=best,
                reason="Several products match this name about equally well.",
            )
        return RowMatch(
            match_status=ImportMatchStatus.FUZZY_CANDIDATE,
            matched_product_id=best_id,
            candidate_product_ids=[c[0] for c in candidates],
            match_confidence=best,
            reason="Matched on name, not on a part code.",
        )
