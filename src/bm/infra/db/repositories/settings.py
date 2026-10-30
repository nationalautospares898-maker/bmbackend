"""Data access for the `settings` table."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from bm.infra.db.models.tenancy import NIL_BRANCH_SCOPE_SQL, Setting


@dataclass(frozen=True, slots=True)
class SettingRow:
    key: str
    value: Any
    value_type: str
    branch_id: uuid.UUID | None
    updated_at: datetime
    updated_by_user_id: uuid.UUID | None


class SettingsRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def all_for(self, business_id: uuid.UUID, branch_id: uuid.UUID | None) -> dict[str, SettingRow]:
        """Business-wide rows overlaid by branch-specific ones.

        A branch override wins. Returning both and resolving here keeps the precedence
        rule in one place instead of at every read site.
        """
        query = select(Setting).where(Setting.business_id == business_id)
        rows = self._session.execute(query).scalars().all()

        resolved: dict[str, SettingRow] = {}
        for row in sorted(rows, key=lambda r: r.branch_id is not None):
            if row.branch_id is not None and row.branch_id != branch_id:
                continue
            resolved[row.key] = SettingRow(
                key=row.key,
                value=row.value,
                value_type=row.value_type,
                branch_id=row.branch_id,
                updated_at=row.updated_at,
                updated_by_user_id=row.updated_by_user_id,
            )
        return resolved

    def get(
        self, business_id: uuid.UUID, branch_id: uuid.UUID | None, key: str
    ) -> SettingRow | None:
        return self.all_for(business_id, branch_id).get(key)

    def upsert(
        self,
        *,
        business_id: uuid.UUID,
        branch_id: uuid.UUID | None,
        key: str,
        value: Any,
        value_type: str,
        updated_by_user_id: uuid.UUID,
    ) -> None:
        """Upsert against the expression unique index (fix S-3).

        The conflict target has to be the same `coalesce(branch_id, <nil>)` expression
        the index uses, because a plain column list would not match it.
        """
        statement = (
            pg_insert(Setting)
            .values(
                id=uuid.uuid4(),
                business_id=business_id,
                branch_id=branch_id,
                key=key,
                value=value,
                value_type=value_type,
                updated_by_user_id=updated_by_user_id,
            )
            .on_conflict_do_update(
                index_elements=[
                    Setting.business_id,
                    # Mirrors `uq_settings__scope_key` exactly; a plain column list
                    # would not match the expression index.
                    text(NIL_BRANCH_SCOPE_SQL),
                    Setting.key,
                ],
                set_={
                    "value": value,
                    "value_type": value_type,
                    "updated_by_user_id": updated_by_user_id,
                },
            )
        )
        self._session.execute(statement)
