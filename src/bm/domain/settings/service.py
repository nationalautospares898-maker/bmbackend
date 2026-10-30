"""Settings resolution and change auditing.

Resolution order: branch row → business row → **code default**. The code default is the
authority for an unset key (`DATA-MODEL.md §3.1`), so a fresh database behaves
identically to a configured one and there is exactly one place that knows what
`tax.mode` means when nobody has set it.

Every change is audited with before/after values (FR-AUDIT-003). A settings change moves
money — a discount cap or a credit term is a business rule — so "who changed this and
when" has to be answerable.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy.orm import Session

from bm.domain.audit import service as audit
from bm.domain.audit.service import AuditAction
from bm.domain.auth.permissions import Permission
from bm.domain.settings.defaults import (
    SPECS,
    SettingKey,
    SettingSpec,
    ValueType,
    coerce,
    spec_for,
)
from bm.domain.shared.context import AuthContext
from bm.domain.shared.money import MoneyPolicy, RoundingMode
from bm.infra.db.repositories.settings import SettingsRepository


@dataclass(frozen=True, slots=True)
class ResolvedSetting:
    key: str
    value: Any
    value_type: str
    description: str
    is_default: bool
    allowed: tuple[str, ...] | None


class SettingsService:
    def __init__(self, session: Session) -> None:
        self._session = session
        self._repo = SettingsRepository(session)

    # --- reading -------------------------------------------------------------

    def resolve_all(
        self, business_id: uuid.UUID, branch_id: uuid.UUID | None
    ) -> dict[SettingKey, Any]:
        stored = self._repo.all_for(business_id, branch_id)
        return {
            key: self._typed(spec, stored[str(key)].value) if str(key) in stored else spec.default
            for key, spec in SPECS.items()
        }

    def get(self, business_id: uuid.UUID, branch_id: uuid.UUID | None, key: SettingKey) -> Any:
        return self.resolve_all(business_id, branch_id)[key]

    def list_for(self, ctx: AuthContext) -> list[ResolvedSetting]:
        ctx.require(Permission.ADMIN_SETTINGS_MANAGE)
        stored = self._repo.all_for(ctx.business_id, ctx.branch_id)
        return [
            ResolvedSetting(
                key=str(key),
                value=self._typed(spec, stored[str(key)].value)
                if str(key) in stored
                else spec.default,
                value_type=str(spec.value_type),
                description=spec.description,
                is_default=str(key) not in stored,
                allowed=spec.allowed,
            )
            for key, spec in SPECS.items()
        ]

    @staticmethod
    def _typed(spec: SettingSpec, raw: Any) -> Any:
        """Rehydrate a JSONB value into its declared type.

        A decimal is stored as a string so JSONB cannot turn it into a float — the same
        reason money crosses the API as a string (ADR-013).
        """
        if spec.value_type is ValueType.DECIMAL:
            return Decimal(str(raw))
        return raw

    # --- writing -------------------------------------------------------------

    def set(self, ctx: AuthContext, key: str, raw_value: Any) -> ResolvedSetting:
        ctx.require(Permission.ADMIN_SETTINGS_MANAGE)

        spec = spec_for(key)
        value = coerce(spec, raw_value)

        before = self.get(ctx.business_id, ctx.branch_id, spec.key)

        # Decimals are persisted as strings for the reason in `_typed`.
        stored_value = str(value) if spec.value_type is ValueType.DECIMAL else value
        self._repo.upsert(
            business_id=ctx.business_id,
            branch_id=ctx.branch_id,
            key=str(spec.key),
            value=stored_value,
            value_type=str(spec.value_type),
            updated_by_user_id=ctx.user_id,
        )

        # `tax.mode` gets its own audit action because enabling GST is the production
        # readiness gate (FR-TAX-003) and must be greppable in the log on its own.
        action = (
            AuditAction.TAX_MODE_CHANGED
            if spec.key is SettingKey.TAX_MODE
            else AuditAction.SETTING_CHANGED
        )
        audit.record_for(
            self._session,
            ctx,
            action=action,
            entity_type="setting",
            reason=f"{spec.key} changed",
            before={"key": str(spec.key), "value": str(before)},
            after={"key": str(spec.key), "value": str(value)},
        )

        return ResolvedSetting(
            key=str(spec.key),
            value=value,
            value_type=str(spec.value_type),
            description=spec.description,
            is_default=False,
            allowed=spec.allowed,
        )

    # --- money policy ---------------------------------------------------------

    def money_policy(self, business_id: uuid.UUID, branch_id: uuid.UUID | None) -> MoneyPolicy:
        """Build the money policy object from settings.

        This is what `GET /sync/bootstrap` publishes. The client takes the policy from
        here and never hardcodes a rounding rule (`../bmfrontend/CLAUDE.md §5` rule 15).

        The version is derived from the policy-affecting values rather than stored, so
        it cannot silently disagree with the rules actually in force. Only `1` — the
        documented v1 default — is reachable without a recorded decision changing a
        settings row.
        """
        resolved = self.resolve_all(business_id, branch_id)
        mode = RoundingMode(resolved[SettingKey.SALES_ROUNDING_MODE])
        whole_rupee = bool(resolved[SettingKey.SALES_ROUND_GRAND_TOTAL_TO_RUPEE])

        version = _POLICY_VERSIONS.get((mode, whole_rupee))
        if version is None:
            # An unlisted combination means someone added a rounding mode without
            # assigning it a version. Refusing is safer than issuing invoices stamped
            # with a version that does not identify the rule that produced them.
            from bm.domain.shared.errors import ValidationFailed

            raise ValidationFailed(
                {
                    "sales.rounding_mode": (
                        "This rounding combination has no recorded policy version. "
                        "Record the policy change before using it."
                    )
                }
            )

        return MoneyPolicy(
            version=version,
            rounding_mode=mode,
            round_grand_total_to_rupee=whole_rupee,
            require_integral_amounts=mode is RoundingMode.REJECT_FRACTIONAL,
            display_decimal_places=0,
        )


#: `(rounding_mode, round_grand_total_to_rupee)` → policy version. Persisted on every
#: invoice (T3-03) so a disputed historical bill can be explained under the rule that
#: produced it. Versions are never reused or renumbered.
_POLICY_VERSIONS: dict[tuple[RoundingMode, bool], int] = {
    (RoundingMode.REJECT_FRACTIONAL, False): 1,  # the documented v1 default
    (RoundingMode.LINE_HALF_UP, False): 2,
    (RoundingMode.LINE_HALF_UP, True): 3,
    (RoundingMode.REJECT_FRACTIONAL, True): 4,
}
