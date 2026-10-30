"""idempotency, sync, queue, outbox, notifications and imports

Revision ID: 0015
Revises: 0014
Created: 2026-10-30

`uq_idempotency__scope` is **UNIQUE and non-deferrable**: it is the
serialisation point for two simultaneous same-key requests, and the entire
duplicate-prevention design depends on one of them losing (`Blueprint 5.2.2`).

`ck_import_rows__decision_required` encodes FR-IMP-007 - a row that is ambiguous, new,
duplicated in the file or invalid cannot be applied without a per-row human decision.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APP_ROLE = "bm_app"


def _app_role_exists() -> bool:
    """Local development sometimes runs everything as one role.

    The grants are skipped rather than failing the migration, and the privilege tests
    skip with a clear reason when the role is absent - so nobody sees a green
    immutability test that proved nothing.
    """
    bind = op.get_bind()
    return bool(
        bind.execute(
            sa.text("SELECT 1 FROM pg_roles WHERE rolname = :role"), {"role": APP_ROLE}
        ).first()
    )


# Every ENUM type was created in migration 0001, so each reference here carries
# `create_type=False`. Without it Alembic would emit a `CREATE TYPE` per column and
# the migration would fail on the second table that uses the same type.


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("endpoint", sa.String(length=64), nullable=False),
        sa.Column("key", sa.String(length=64), nullable=False),
        sa.Column("request_fingerprint", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "in_progress", "completed", name="idempotency_status", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("response_status", sa.Integer(), nullable=True),
        sa.Column("response_body", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("resource_type", sa.String(length=32), nullable=True),
        sa.Column("resource_id", sa.UUID(), nullable=True),
        sa.Column("user_id", sa.UUID(), nullable=True),
        sa.Column("device_id", sa.UUID(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(status = 'completed') = (completed_at IS NOT NULL)", name="ck_idempotency__completed"
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "endpoint", "key", name="uq_idempotency__scope"),
    )
    op.create_index(
        "ix_idempotency_keys__created", "idempotency_keys", ["created_at"], unique=False
    )
    op.create_table(
        "sync_operations",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("device_id", sa.UUID(), nullable=False),
        sa.Column("user_id", sa.UUID(), nullable=False),
        sa.Column("operation_type", sa.String(length=32), nullable=False),
        sa.Column("entity_type", sa.String(length=32), nullable=False),
        sa.Column("entity_id", sa.UUID(), nullable=False),
        sa.Column("client_seq", sa.BigInteger(), nullable=False),
        sa.Column("base_version", sa.Integer(), nullable=True),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "accepted",
                "replayed",
                "conflicted",
                "rejected",
                name="sync_operation_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column("error_detail", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("client_created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "server_received_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["device_id"], ["devices.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_sync_operations__device_seq",
        "sync_operations",
        ["device_id", "client_seq"],
        unique=False,
    )
    op.create_index(
        "ix_sync_operations__entity",
        "sync_operations",
        ["entity_type", "entity_id", sa.literal_column("server_received_at DESC")],
        unique=False,
    )
    op.create_table(
        "jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("kind", sa.String(length=64), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "queued",
                "running",
                "succeeded",
                "failed",
                "dead",
                name="job_status",
                create_type=False,
            ),
            server_default=sa.text("'queued'::job_status"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("max_attempts", sa.Integer(), server_default=sa.text("5"), nullable=False),
        sa.Column(
            "run_after", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False
        ),
        sa.Column("locked_by", sa.String(length=64), nullable=True),
        sa.Column("locked_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.String(length=2000), nullable=True),
        sa.Column("dedupe_key", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_jobs__claim",
        "jobs",
        ["status", "run_after"],
        unique=False,
        postgresql_where=sa.text("status = 'queued'"),
    )
    op.create_index(
        "uq_jobs__dedupe",
        "jobs",
        ["dedupe_key"],
        unique=True,
        postgresql_where=sa.text("dedupe_key IS NOT NULL"),
    )
    op.create_table(
        "outbox_events",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("aggregate_type", sa.String(length=32), nullable=False),
        sa.Column("aggregate_id", sa.UUID(), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "pending", "dispatched", "failed", name="outbox_status", create_type=False
            ),
            server_default=sa.text("'pending'::outbox_status"),
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("dispatched_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_outbox_events__pending",
        "outbox_events",
        ["status", "created_at"],
        unique=False,
        postgresql_where=sa.text("status = 'pending'"),
    )
    op.create_table(
        "notifications",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column(
            "kind",
            postgresql.ENUM(
                "low_stock",
                "overdue_credit",
                "reconciliation_alert",
                "import_complete",
                name="notification_kind",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "severity",
            postgresql.ENUM(
                "info", "warning", "critical", name="notification_severity", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("body", sa.String(length=2000), nullable=True),
        sa.Column("entity_type", sa.String(length=32), nullable=True),
        sa.Column("entity_id", sa.UUID(), nullable=True),
        sa.Column("read_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_notifications__unread",
        "notifications",
        ["business_id", sa.literal_column("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("read_at IS NULL"),
    )
    op.create_table(
        "import_jobs",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("business_id", sa.UUID(), nullable=False),
        sa.Column("branch_id", sa.UUID(), nullable=False),
        sa.Column(
            "intent",
            postgresql.ENUM(
                "price_list", "purchase_receipt", name="import_intent", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("supplier_id", sa.UUID(), nullable=True),
        sa.Column("original_filename", sa.String(length=200), nullable=False),
        sa.Column("stored_object_key", sa.String(length=256), nullable=False),
        sa.Column("content_type", sa.String(length=128), nullable=False),
        sa.Column("file_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("file_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                "uploaded",
                "parsing",
                "parsed",
                "needs_review",
                "applying",
                "applied",
                "failed",
                "cancelled",
                name="import_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("duplicate_of_job_id", sa.UUID(), nullable=True),
        sa.Column("duplicate_override_reason", sa.String(length=500), nullable=True),
        sa.Column("row_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_applied", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_skipped", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("rows_failed", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("error_summary", sa.String(length=2000), nullable=True),
        sa.Column("created_purchase_id", sa.UUID(), nullable=True),
        sa.Column("uploaded_by_user_id", sa.UUID(), nullable=False),
        sa.Column("applied_by_user_id", sa.UUID(), nullable=True),
        sa.Column("applied_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "intent <> 'purchase_receipt' OR supplier_id IS NOT NULL",
            name="ck_import_jobs__supplier",
        ),
        sa.ForeignKeyConstraint(["applied_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["branch_id"], ["branches.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["business_id"], ["businesses.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_purchase_id"], ["purchases.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["duplicate_of_job_id"], ["import_jobs.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["uploaded_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("business_id", "file_sha256", name="uq_import_jobs__file"),
    )
    op.create_table(
        "import_rows",
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column("import_job_id", sa.UUID(), nullable=False),
        sa.Column("row_index", sa.Integer(), nullable=False),
        sa.Column("raw_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("parsed_name", sa.String(length=200), nullable=True),
        sa.Column("parsed_sku", sa.String(length=64), nullable=True),
        sa.Column("parsed_quantity", sa.Numeric(precision=14, scale=3), nullable=True),
        sa.Column("parsed_unit_cost", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column("parsed_selling_price", sa.Numeric(precision=14, scale=4), nullable=True),
        sa.Column(
            "match_status",
            postgresql.ENUM(
                "exact_sku",
                "exact_alias",
                "fuzzy_candidate",
                "ambiguous",
                "new_product",
                "duplicate_in_file",
                "invalid",
                name="import_match_status",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("matched_product_id", sa.UUID(), nullable=True),
        sa.Column("candidate_product_ids", postgresql.ARRAY(sa.UUID()), nullable=True),
        sa.Column("match_confidence", sa.Numeric(), nullable=True),
        sa.Column(
            "decision",
            postgresql.ENUM(
                "pending",
                "accept_match",
                "create_new",
                "skip",
                name="import_decision",
                create_type=False,
            ),
            server_default=sa.text("'pending'::import_decision"),
            nullable=False,
        ),
        sa.Column("decided_by_user_id", sa.UUID(), nullable=True),
        sa.Column(
            "apply_result",
            postgresql.ENUM(
                "applied", "skipped", "failed", name="import_apply_result", create_type=False
            ),
            nullable=True,
        ),
        sa.Column("apply_error", sa.String(length=2000), nullable=True),
        sa.CheckConstraint(
            "match_status NOT IN ('ambiguous','new_product','duplicate_in_file','invalid') OR decision <> 'pending' OR apply_result IS NULL",
            name="ck_import_rows__decision_required",
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["import_job_id"], ["import_jobs.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["matched_product_id"], ["products.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("import_job_id", "row_index", name="uq_import_rows__row"),
    )
    op.create_index(
        "ix_import_rows__status", "import_rows", ["import_job_id", "match_status"], unique=False
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_import_rows__status")
    op.drop_table("import_rows")
    op.drop_table("import_jobs")
    op.execute("DROP INDEX IF EXISTS ix_notifications__unread")
    op.drop_table("notifications")
    op.execute("DROP INDEX IF EXISTS ix_outbox_events__pending")
    op.drop_table("outbox_events")
    op.execute("DROP INDEX IF EXISTS ix_jobs__claim")
    op.execute("DROP INDEX IF EXISTS uq_jobs__dedupe")
    op.drop_table("jobs")
    op.execute("DROP INDEX IF EXISTS ix_sync_operations__device_seq")
    op.execute("DROP INDEX IF EXISTS ix_sync_operations__entity")
    op.drop_table("sync_operations")
    op.execute("DROP INDEX IF EXISTS ix_idempotency_keys__created")
    op.drop_table("idempotency_keys")
