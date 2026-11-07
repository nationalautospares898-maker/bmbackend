"""Mechanic reference list — search, and get-or-create.

The shape of this module is set by one requirement: the counter must be able to type a
mechanic's name on a bill and have it be there, spelled the same way, on the next bill —
without anybody visiting a "manage mechanics" screen first. So the write path is a
**get-or-create keyed on the normalised name**, not a create that can fail with "already
exists".

## Why `ensure` cannot be a plain `INSERT`

Two counters billing the same mechanic at the same moment both see "not found" and both
insert. `uq_mechanics__business_name` then fails one of them, and the cashier who lost
the race gets an error for doing nothing wrong. `ON CONFLICT … DO UPDATE` collapses that
to one row and returns it to both, which is why the insert is written as an upsert rather
than guarded by a prior `SELECT`. The prior `SELECT` is still there, but only as the
cheap path — it is not the correctness mechanism.

That also makes the endpoint naturally idempotent: pressing Save twice, or a retried
request after a dropped connection, yields the same single mechanic and the same
response. No `Idempotency-Key` is required, because nothing here moves money or stock
(invariant 9 lists the endpoints that do).

## Why `last_used_at` is bumped on `ensure` and not on search

Suggestions are ordered most-recently-used first. "Used" means *put on a bill*, which is
what `ensure` is called for — not *looked at*, which is what a search is. Bumping on
search would let scrolling a list reorder it underneath the person scrolling.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.shared.context import AuthContext
from bm.domain.shared.errors import NotFound, ValidationFailed
from bm.domain.shared.normalize import escape_like, normalize_name, normalize_phone

#: The suggestion dropdown shows a handful; the cap stops a crafted `limit` turning an
#: autocomplete into a full-table export.
MAX_SEARCH_LIMIT = 50


def _has_letter_or_digit(value: str) -> bool:
    """At least one alphanumeric character.

    Unicode-aware via `str.isalnum`, so a Devanagari or Tamil name qualifies — an
    ASCII-only test would reject names this shop plausibly writes.
    """
    return any(character.isalnum() for character in value)


@dataclass(frozen=True, slots=True)
class MechanicView:
    id: uuid.UUID
    name: str
    phone: str | None
    is_active: bool
    use_count: int
    last_used_at: datetime | None


@dataclass(frozen=True, slots=True)
class EnsureResult:
    mechanic: MechanicView
    #: `False` when an existing mechanic was matched, so the caller can tell the
    #: difference between "saved a new name" and "reused one".
    created: bool


def _view(row: object) -> MechanicView:
    r = row  # a SQLAlchemy Row; attribute access is checked by the query's own columns.
    return MechanicView(
        id=r.id,  # type: ignore[attr-defined]
        name=r.name,  # type: ignore[attr-defined]
        phone=r.phone,  # type: ignore[attr-defined]
        is_active=r.is_active,  # type: ignore[attr-defined]
        use_count=r.use_count,  # type: ignore[attr-defined]
        last_used_at=r.last_used_at,  # type: ignore[attr-defined]
    )


_COLUMNS = "id, name, phone, is_active, use_count, last_used_at"


class MechanicService:
    def __init__(self, session: Session) -> None:
        self._session = session

    # --- reads ----------------------------------------------------------------

    def search(
        self, ctx: AuthContext, *, query: str | None = None, limit: int = 20
    ) -> list[MechanicView]:
        """Prefix match, then fuzzy, ordered most-recently-used first.

        An **empty query is legitimate** and returns the recently-used list. The box is
        focused before anything is typed, and showing the three names that account for
        most bills right then is the whole point of keeping `last_used_at`.
        """
        ctx.require(Permission.CUSTOMER_MANAGE)
        effective = min(limit or 20, MAX_SEARCH_LIMIT)
        term = (query or "").strip()

        if not term:
            rows = self._session.execute(
                text(
                    f"SELECT {_COLUMNS} FROM mechanics"
                    "  WHERE business_id = :business_id AND is_active"
                    "  ORDER BY last_used_at DESC NULLS LAST, use_count DESC, name ASC"
                    "  LIMIT :limit"
                ),
                {"business_id": ctx.business_id, "limit": effective},
            ).all()
            return [_view(r) for r in rows]

        normalized = normalize_name(term)
        rows = self._session.execute(
            text(
                f"SELECT {_COLUMNS} FROM mechanics"
                "  WHERE business_id = :business_id AND is_active"
                "    AND (normalized_name LIKE :prefix OR normalized_name % :raw)"
                # Prefix matches first: someone typing "ram" means Ramesh before
                # Sriram, and a trigram score alone does not know that.
                "  ORDER BY (normalized_name LIKE :prefix) DESC,"
                "           last_used_at DESC NULLS LAST, use_count DESC, name ASC"
                "  LIMIT :limit"
            ),
            {
                "business_id": ctx.business_id,
                "prefix": f"{escape_like(normalized)}%",
                "raw": normalized,
                "limit": effective,
            },
        ).all()
        return [_view(r) for r in rows]

    def get(self, ctx: AuthContext, mechanic_id: uuid.UUID) -> MechanicView:
        row = self._session.execute(
            text(
                f"SELECT {_COLUMNS} FROM mechanics  WHERE id = :id AND business_id = :business_id"
            ),
            {"id": mechanic_id, "business_id": ctx.business_id},
        ).first()
        if row is None:
            # `404` and not `403` for another tenant's row — never leak existence.
            raise NotFound("mechanic")
        return _view(row)

    # --- writes ---------------------------------------------------------------

    def ensure(self, ctx: AuthContext, *, name: str, phone: str | None = None) -> EnsureResult:
        """Get-or-create by normalised name, and mark it used.

        Called from the billing flow, so it must tolerate being called with a name that
        already exists — which is the common case, and is not an error.
        """
        ctx.require(Permission.CUSTOMER_MANAGE)

        display = " ".join(name.split())
        if not display:
            raise ValidationFailed({"name": "Enter a mechanic's name."})
        if len(display) > 200:
            raise ValidationFailed({"name": "Use 200 characters or fewer."})
        normalized = normalize_name(display)
        if not normalized or not _has_letter_or_digit(normalized):
            # Whitespace-only normalises away to nothing. Punctuation-only (`...`) does
            # **not** — `normalize_name` collapses whitespace and strips accents but
            # keeps punctuation — so it needs its own check, or `...` becomes a
            # permanent entry in the suggestion list.
            #
            # The rule is "at least one letter or digit", deliberately not "no
            # punctuation": initials are how plenty of mechanics are actually recorded,
            # so `A.R.` and `K.P.` must keep working.
            raise ValidationFailed({"name": "Enter a mechanic's name."})

        existing = self._session.execute(
            text(
                f"SELECT {_COLUMNS} FROM mechanics"
                "  WHERE business_id = :business_id AND normalized_name = :normalized"
            ),
            {"business_id": ctx.business_id, "normalized": normalized},
        ).first()

        if existing is not None:
            row = self._session.execute(
                text(
                    "UPDATE mechanics"
                    "   SET last_used_at = now(), use_count = use_count + 1,"
                    # Reactivated by use: a mechanic who comes back should not need an
                    # admin screen to reappear in the suggestions.
                    "       is_active = true, updated_at = now()"
                    " WHERE id = :id AND business_id = :business_id"
                    f" RETURNING {_COLUMNS}"
                ),
                {"id": existing.id, "business_id": ctx.business_id},
            ).first()
            assert row is not None
            return EnsureResult(mechanic=_view(row), created=False)

        # The upsert, not the `SELECT` above, is what makes two counters racing safe.
        row = self._session.execute(
            text(
                "INSERT INTO mechanics"
                "  (id, business_id, name, normalized_name, phone, created_by_user_id,"
                "   last_used_at, use_count)"
                " VALUES (:id, :business_id, :name, :normalized, :phone, :user_id,"
                "   now(), 1)"
                " ON CONFLICT (business_id, normalized_name) DO UPDATE"
                "   SET last_used_at = now(), use_count = mechanics.use_count + 1,"
                "       is_active = true, updated_at = now()"
                f" RETURNING {_COLUMNS}, (xmax = 0) AS inserted"
            ),
            {
                "id": uuid.uuid4(),
                "business_id": ctx.business_id,
                "name": display,
                "normalized": normalized,
                "phone": normalize_phone(phone) if phone else None,
                "user_id": ctx.user_id,
            },
        ).first()
        assert row is not None
        # `xmax = 0` distinguishes a real insert from a conflict-update, so `created`
        # is the truth rather than an assumption.
        created = bool(row.inserted)
        view = _view(row)

        if created:
            audit.record_for(
                self._session,
                ctx,
                action=AuditAction.MECHANIC_CREATED,
                entity_type="mechanic",
                entity_id=view.id,
                after={"name": view.name},
            )
        return EnsureResult(mechanic=view, created=created)

    def rename(self, ctx: AuthContext, mechanic_id: uuid.UUID, *, name: str) -> MechanicView:
        """Correct a spelling.

        Safe precisely because no invoice points here: `invoices.mechanic_name` is a
        snapshot, so a correction changes what the *next* bill suggests and nothing
        about what a past bill says.
        """
        ctx.require(Permission.CUSTOMER_MANAGE)
        before = self.get(ctx, mechanic_id)

        display = " ".join(name.split())
        normalized = normalize_name(display)
        if not display or not normalized:
            raise ValidationFailed({"name": "Enter a mechanic's name."})
        if len(display) > 200:
            raise ValidationFailed({"name": "Use 200 characters or fewer."})

        clash = self._session.execute(
            text(
                "SELECT id FROM mechanics"
                " WHERE business_id = :business_id AND normalized_name = :normalized"
                "   AND id <> :id"
            ),
            {"business_id": ctx.business_id, "normalized": normalized, "id": mechanic_id},
        ).first()
        if clash is not None:
            # Surfaced rather than silently merged. Two mechanics are not provably one
            # person just because a rename made their names collide — the same stance
            # customers take (FR-CUST-004).
            raise ValidationFailed({"name": "A mechanic with that name already exists."})

        row = self._session.execute(
            text(
                "UPDATE mechanics SET name = :name, normalized_name = :normalized,"
                "   updated_at = now()"
                " WHERE id = :id AND business_id = :business_id"
                f" RETURNING {_COLUMNS}"
            ),
            {
                "id": mechanic_id,
                "business_id": ctx.business_id,
                "name": display,
                "normalized": normalized,
            },
        ).first()
        assert row is not None
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.MECHANIC_UPDATED,
            entity_type="mechanic",
            entity_id=mechanic_id,
            before={"name": before.name},
            after={"name": display},
        )
        return _view(row)

    def deactivate(self, ctx: AuthContext, mechanic_id: uuid.UUID) -> None:
        """Drop someone from the suggestions without deleting them.

        A delete would be wrong even though nothing references the row: the audit trail
        of who created it, and the name itself, are the only record that the person was
        ever on the list.
        """
        ctx.require(Permission.CUSTOMER_MANAGE)
        before = self.get(ctx, mechanic_id)
        self._session.execute(
            text(
                "UPDATE mechanics SET is_active = false, updated_at = now()"
                " WHERE id = :id AND business_id = :business_id"
            ),
            {"id": mechanic_id, "business_id": ctx.business_id},
        )
        audit.record_for(
            self._session,
            ctx,
            action=AuditAction.MECHANIC_DEACTIVATED,
            entity_type="mechanic",
            entity_id=mechanic_id,
            before={"name": before.name, "is_active": True},
            after={"is_active": False},
        )
