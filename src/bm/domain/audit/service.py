"""Audit trail.

`SECURITY.md §9`: every state-changing operation writes an `audit_logs` row **in the
same transaction as the change it describes**, so an audited action cannot commit
without its audit row. The table is append-only at the privilege level, so this service
has no update or delete path to offer.

Before/after values are recorded for the sensitive actions listed in FR-AUDIT-003.
Secrets never are (FR-AUDIT-004) — `redact_value` is applied to both.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import insert
from sqlalchemy.orm import Session

from bm.domain.shared.context import AuthContext
from bm.infra.db.models.identity import AuditLog
from bm.infra.logging.setup import redact_value, request_id_var


class AuditAction(StrEnum):
    """The audit-action vocabulary.

    ⚠️ These spellings are deliberately **not** the permission or settings spellings of
    the same concepts. `docs/DATA-MODEL.md §3.14` is the authoritative table; using a
    value from one vocabulary where another is expected is a defect, not a style choice.
    """

    # auth
    USER_LOGGED_IN = "user.logged_in"
    USER_LOGIN_FAILED = "user.login_failed"
    USER_LOGGED_OUT = "user.logged_out"
    USER_LOCKED_OUT = "user.locked_out"
    USER_PASSWORD_CHANGED = "user.password_changed"
    USER_PASSWORD_RESET = "user.password_reset"
    USER_ACTIVATED = "user.activated"
    USER_DEACTIVATED = "user.deactivated"
    REFRESH_TOKEN_REUSED = "refresh_token.reused"
    REFRESH_FAMILY_REVOKED = "refresh_family.revoked"
    DEVICE_REGISTERED = "device.registered"
    DEVICE_REVOKED = "device.revoked"
    #: Which counter a registered PC bills as. Audited separately from
    #: `COUNTER_UPDATED` because it answers a different question: not "what changed
    #: about this till" but "whose takings does this machine's next sale land in".
    DEVICE_COUNTER_ASSIGNED = "device.counter_assigned"
    BRANCH_SELECTED = "branch.selected"
    ROLE_CHANGED = "role.changed"

    # settings and tax
    SETTING_CHANGED = "setting.changed"
    TAX_MODE_CHANGED = "tax.mode_changed"
    BUSINESS_UPDATED = "business.updated"
    COUNTER_UPDATED = "counter.updated"
    INVOICE_SERIES_UPDATED = "invoice_series.updated"

    # catalog
    PRODUCT_CREATED = "product.created"
    PRODUCT_UPDATED = "product.updated"
    PRODUCT_PRICE_OVERRIDDEN = "product.price_overridden"
    PRODUCT_DEACTIVATED = "product.deactivated"

    # inventory
    INVENTORY_ADJUSTED = "inventory.adjusted"
    INVENTORY_OVERSELL_OVERRIDE = "inventory.oversell_override"
    STOCK_COUNT_APPLIED = "stock_count.applied"

    # sales
    SALE_FINALIZED = "sale.finalized"
    INVOICE_CANCELLED = "invoice.cancelled"
    INVOICE_DISCOUNT_APPLIED = "invoice.discount_applied"
    INVOICE_PRINTED = "invoice.printed"
    DRAFT_TAKEN_OVER = "draft.taken_over"
    DRAFT_CANCELLED = "draft.cancelled"

    # credit
    PAYMENT_RECORDED = "payment.recorded"
    PAYMENT_REVERSED = "payment.reversed"
    CREDIT_WRITTEN_OFF = "credit.written_off"
    CREDIT_NOTE_ISSUED = "credit_note.issued"
    CREDIT_NOTE_ALLOCATED = "credit_note.allocated"

    # customers
    CUSTOMER_CREATED = "customer.created"
    CUSTOMER_UPDATED = "customer.updated"
    CUSTOMER_DEACTIVATED = "customer.deactivated"

    # purchases, returns, imports
    PURCHASE_CONFIRMED = "purchase.confirmed"
    PURCHASE_CANCELLED = "purchase.cancelled"
    RETURN_CREATED = "return.created"
    IMPORT_UPLOADED = "import.uploaded"
    IMPORT_APPLIED = "import.applied"
    IMPORT_DUPLICATE_OVERRIDDEN = "import.duplicate_overridden"


def record(
    session: Session,
    *,
    business_id: uuid.UUID,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    actor_user_id: uuid.UUID | None = None,
    branch_id: uuid.UUID | None = None,
    device_id: uuid.UUID | None = None,
    counter_id: uuid.UUID | None = None,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> None:
    """Append one audit row using the caller's session.

    This never commits. It participates in the caller's transaction, which is the whole
    point: if the business change rolls back, so does its audit row, and the log never
    claims something happened that did not.
    """
    session.execute(
        insert(AuditLog).values(
            id=uuid.uuid4(),
            business_id=business_id,
            branch_id=branch_id,
            actor_user_id=actor_user_id,
            device_id=device_id,
            counter_id=counter_id,
            action=str(action),
            entity_type=entity_type,
            entity_id=entity_id,
            reason=reason,
            before_value=redact_value(before) if before is not None else None,
            after_value=redact_value(after) if after is not None else None,
            request_id=request_id_var.get(),
            ip_hash=ip_hash,
        )
    )


def record_for(
    session: Session,
    ctx: AuthContext,
    *,
    action: AuditAction,
    entity_type: str,
    entity_id: uuid.UUID | None = None,
    reason: str | None = None,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    ip_hash: str | None = None,
) -> None:
    """`record()` with the actor, branch, device and counter taken from the context.

    Most call sites want this: forgetting to pass `device_id` would quietly produce an
    audit row that cannot answer "which counter did this".
    """
    record(
        session,
        business_id=ctx.business_id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        actor_user_id=ctx.user_id,
        branch_id=ctx.branch_id,
        device_id=ctx.device_id,
        counter_id=ctx.counter_id,
        reason=reason,
        before=before,
        after=after,
        ip_hash=ip_hash,
    )
