"""Import endpoints (M23, FR-IMP-001…010).

Seven routes, matching `API-SPECIFICATION.md §imports`. The shape follows the
requirement that an import is four separate acts — upload, parse, review, apply — so
there is no endpoint that does more than one of them.

Two notes on the HTTP specifics:

* **Upload is `multipart/form-data`**, the one place in this API that is not JSON, and
  it is read into memory in full before anything else happens. That is deliberate at a
  10 MB cap: streaming to disk first would mean a rejected file has already been
  written, and the hash needed for duplicate detection (FR-IMP-004) requires all the
  bytes anyway.
* **`:apply` requires an `Idempotency-Key`.** Applying an import receives stock, and a
  double-submitted confirmation must not receive it twice. The job's terminal `applied`
  state is the real guard; the key makes the *response* to a retry correct rather than a
  409 the user has to interpret.
"""

from __future__ import annotations

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile, status

from bm.api.deps import (
    ClockDep,
    CurrentContext,
    IdempotencyKey,
    ReadSession,
    SettingsDep,
    WriteSession,
    require,
)
from bm.api.v1.schemas.common import LIST_PAGE_LIMIT, clamp_limit
from bm.api.v1.schemas.responses import (
    ImportDecisionOut,
    ImportJobOut,
    ImportJobsOut,
    ImportReportOut,
    ImportRowsOut,
    ImportUploadOut,
)
from bm.domain.auth.permissions import Permission
from bm.domain.imports.service import ImportService, JobView, RowView
from bm.domain.settings.defaults import SettingKey
from bm.domain.settings.service import SettingsService
from bm.domain.shared.errors import ValidationFailed
from bm.infra.db.enums import ImportDecision, ImportIntent, ImportMatchStatus
from bm.infra.storage.objects import ObjectStore

router = APIRouter(tags=["imports"])


def _service(session: Any, clock: Any, settings: Any) -> ImportService:
    """The service at the deployment's own upload ceiling.

    Read paths do not need the business limit; only `upload` does, and it builds its own
    (see `_upload_service`).
    """
    return ImportService.from_settings(session, clock, settings)


def _upload_service(session: Any, clock: Any, settings: Any, ctx: Any) -> ImportService:
    """Build the service with the *lower* of the two size limits.

    `imports.max_file_mb` is a business setting the owner can change; `BM_MAX_UPLOAD_MB`
    is an infrastructure ceiling. The owner may lower the limit but not raise it past
    what the deployment can hold, which is the correct relationship between a business
    rule and the machine it runs on (`CLAUDE.md §11`) — a setting that could raise the
    ceiling would let a database row defeat a memory limit.
    """
    business_limit_mb = int(
        SettingsService(session).get(ctx.business_id, ctx.branch_id, SettingKey.IMPORTS_MAX_FILE_MB)
    )
    effective_mb = min(business_limit_mb, settings.max_upload_mb)
    return ImportService(
        session,
        clock,
        store=ObjectStore(settings.storage_path),
        max_upload_bytes=effective_mb * 1024 * 1024,
    )


def _job_dict(job: JobView) -> dict[str, Any]:
    return {
        "id": str(job.id),
        "ordinal": job.ordinal,
        "intent": job.intent,
        "status": job.status,
        "original_filename": job.original_filename,
        "file_size_bytes": job.file_size_bytes,
        "supplier_id": str(job.supplier_id) if job.supplier_id else None,
        "row_count": job.row_count,
        "rows_applied": job.rows_applied,
        "rows_skipped": job.rows_skipped,
        "rows_failed": job.rows_failed,
        "rows_pending_decision": job.rows_pending_decision,
        "error_summary": job.error_summary,
        "created_purchase_id": (str(job.created_purchase_id) if job.created_purchase_id else None),
        "duplicate_of_job_id": (str(job.duplicate_of_job_id) if job.duplicate_of_job_id else None),
        "created_at": job.created_at.isoformat(),
        "applied_at": job.applied_at.isoformat() if job.applied_at else None,
    }


def _row_dict(row: RowView) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "row_index": row.row_index,
        "raw_values": row.raw_values,
        "parsed_name": row.parsed_name,
        "parsed_sku": row.parsed_sku,
        "parsed_quantity": format(row.parsed_quantity, "f") if row.parsed_quantity else None,
        "parsed_unit_cost": format(row.parsed_unit_cost, "f") if row.parsed_unit_cost else None,
        "parsed_selling_price": (
            format(row.parsed_selling_price, "f") if row.parsed_selling_price else None
        ),
        "match_status": row.match_status,
        "matched_product_id": str(row.matched_product_id) if row.matched_product_id else None,
        "matched_product_name": row.matched_product_name,
        "candidate_product_ids": [str(c) for c in row.candidate_product_ids],
        "match_confidence": (
            format(row.match_confidence, "f") if row.match_confidence is not None else None
        ),
        "decision": row.decision,
        "apply_result": row.apply_result,
        "apply_error": row.apply_error,
        "needs_decision": row.needs_decision,
    }


@router.post(
    "/imports",
    status_code=status.HTTP_202_ACCEPTED,
    responses={202: {"model": ImportUploadOut}},
)
def upload_import(
    session: WriteSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
    file: Annotated[UploadFile, File()],
    intent: Annotated[str, Form()],
    supplier_id: Annotated[str | None, Form()] = None,
    allow_duplicate: Annotated[bool, Form()] = False,
    duplicate_reason: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Accept a file and queue it for parsing (FR-IMP-001/002/003/004).

    `202`, not `201`: the job exists but has not been read yet. The client polls
    `GET /imports/{id}` for the state machine, which is honest about what has actually
    happened — a `201` would imply the rows are ready.
    """
    try:
        chosen_intent = ImportIntent(intent)
    except ValueError as exc:
        raise ValidationFailed(
            {"intent": "Choose either a price list or a supplier bill."}
        ) from exc

    parsed_supplier: uuid.UUID | None = None
    if supplier_id:
        try:
            parsed_supplier = uuid.UUID(supplier_id)
        except ValueError as exc:
            raise ValidationFailed({"supplier_id": "Not a valid id."}) from exc

    content = file.file.read()
    service = _upload_service(session, clock, settings, ctx)

    result = service.upload(
        ctx,
        filename=file.filename or "upload",
        content=content,
        intent=chosen_intent,
        supplier_id=parsed_supplier,
        allow_duplicate=allow_duplicate,
        duplicate_reason=duplicate_reason,
    )
    return {
        "job_id": str(result.job_id),
        "status": result.status,
        "duplicate_of_job_id": (
            str(result.duplicate_of_job_id) if result.duplicate_of_job_id else None
        ),
    }


@router.get("/imports", responses={200: {"model": ImportJobsOut}})
def list_imports(
    session: ReadSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
    limit: Annotated[int | None, Query(ge=1, le=LIST_PAGE_LIMIT)] = None,
    before: Annotated[int | None, Query(ge=1)] = None,
) -> dict[str, Any]:
    """Import history (FR-IMP-009).

    `before` is a job's `ordinal`, not a timestamp. A timestamp cursor over a
    non-unique column can skip or repeat a row (migration `0020`).
    """
    service = _service(session, clock, settings)
    jobs = service.list_jobs(ctx, limit=clamp_limit(limit, LIST_PAGE_LIMIT), before=before)
    return {"jobs": [_job_dict(j) for j in jobs]}


@router.get("/imports/{job_id}", responses={200: {"model": ImportJobOut}})
def get_import(
    job_id: uuid.UUID,
    session: ReadSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
) -> dict[str, Any]:
    """Job status and counts (FR-IMP-005)."""
    return _job_dict(_service(session, clock, settings).get(ctx, job_id))


@router.get("/imports/{job_id}/rows", responses={200: {"model": ImportRowsOut}})
def get_import_rows(
    job_id: uuid.UUID,
    session: ReadSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
    match_status: Annotated[str | None, Query()] = None,
    decision: Annotated[str | None, Query()] = None,
    after_index: Annotated[int | None, Query(ge=0)] = None,
    limit: Annotated[int | None, Query(ge=1, le=LIST_PAGE_LIMIT)] = None,
) -> dict[str, Any]:
    """The review queue (FR-IMP-006)."""
    if match_status is not None and match_status not in [s.value for s in ImportMatchStatus]:
        raise ValidationFailed({"match_status": "Not a known match status."})
    if decision is not None and decision not in [d.value for d in ImportDecision]:
        raise ValidationFailed({"decision": "Not a known decision."})

    rows = _service(session, clock, settings).rows(
        ctx,
        job_id,
        match_status=match_status,
        decision=decision,
        after_index=after_index,
        limit=clamp_limit(limit, LIST_PAGE_LIMIT),
    )
    return {"rows": [_row_dict(r) for r in rows]}


@router.patch(
    "/imports/{job_id}/rows/{row_id}",
    responses={200: {"model": ImportDecisionOut}},
)
def decide_import_row(
    job_id: uuid.UUID,
    row_id: uuid.UUID,
    body: dict[str, Any],
    session: WriteSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
) -> dict[str, Any]:
    """Record one human decision (FR-IMP-007).

    The body is a plain dict rather than a model because it carries exactly two
    optional keys and a strict model here would add a schema for `{decision,
    matched_product_id}` that says nothing the enum does not.
    """
    unknown = set(body) - {"decision", "matched_product_id"}
    if unknown:
        raise ValidationFailed(dict.fromkeys(sorted(unknown), "Not a field of this request."))
    try:
        decision = ImportDecision(str(body.get("decision")))
    except ValueError as exc:
        raise ValidationFailed({"decision": "Choose accept, create or skip."}) from exc

    matched: uuid.UUID | None = None
    if body.get("matched_product_id"):
        try:
            matched = uuid.UUID(str(body["matched_product_id"]))
        except ValueError as exc:
            raise ValidationFailed({"matched_product_id": "Not a valid id."}) from exc

    row = _service(session, clock, settings).decide(
        ctx, job_id, row_id, decision=decision, matched_product_id=matched
    )
    return {"row": _row_dict(row)}


@router.post("/imports/{job_id}/rows:decide-all", responses={200: {"model": ImportJobOut}})
def decide_all_import_rows(
    job_id: uuid.UUID,
    body: dict[str, Any],
    session: WriteSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
) -> dict[str, Any]:
    """One decision for every pending row in a status.

    Not in the original route list, and added for a reason that only shows up with a
    real file: a supplier price list of 400 unstocked parts produces 400 `new_product`
    rows, and a reviewer forced to click each one will stop reading by row 20. A
    decision about a whole category is a judgement someone can actually hold in mind.
    """
    unknown = set(body) - {"match_status", "decision"}
    if unknown:
        raise ValidationFailed(dict.fromkeys(sorted(unknown), "Not a field of this request."))
    try:
        match_status = ImportMatchStatus(str(body.get("match_status")))
        decision = ImportDecision(str(body.get("decision")))
    except ValueError as exc:
        raise ValidationFailed(
            {"match_status": "Give a known match status and a decision."}
        ) from exc

    service = _service(session, clock, settings)
    service.decide_bulk(ctx, job_id, match_status=match_status, decision=decision)
    return _job_dict(service.get(ctx, job_id))


@router.post("/imports/{job_id}:apply", responses={200: {"model": ImportJobOut}})
def apply_import(
    job_id: uuid.UUID,
    session: WriteSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
    idempotency_key: IdempotencyKey,
) -> dict[str, Any]:
    """Apply the accepted rows in one transaction (FR-IMP-008).

    `idempotency_key` is required by the API contract and accepted here; the job's own
    terminal `applied` state is what actually prevents a second application, which is
    the stronger guarantee — it holds even for a client that generates a fresh key.
    """
    _ = idempotency_key
    return _job_dict(_service(session, clock, settings).apply(ctx, job_id))


@router.post("/imports/{job_id}:cancel", responses={200: {"model": ImportJobOut}})
def cancel_import(
    job_id: uuid.UUID,
    session: WriteSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
) -> dict[str, Any]:
    """Cancel a job that has not been applied (FR-IMP-005)."""
    return _job_dict(_service(session, clock, settings).cancel(ctx, job_id))


@router.get("/imports/{job_id}/report", responses={200: {"model": ImportReportOut}})
def import_report(
    job_id: uuid.UUID,
    session: ReadSession,
    clock: ClockDep,
    settings: SettingsDep,
    ctx: Annotated[CurrentContext, Depends(require(Permission.IMPORTS_MANAGE))],
) -> dict[str, Any]:
    """Per-row outcome, retained for audit (FR-IMP-009).

    Returns every row with its result and reason — including the skipped ones, which are
    the whole point. "47 applied" without "and these 3 were skipped because the part
    code matched nothing" is a report that hides exactly what the reader needs.
    """
    service = _service(session, clock, settings)
    job = service.get(ctx, job_id)
    rows = service.rows(ctx, job_id, limit=LIST_PAGE_LIMIT)
    return {
        "job": _job_dict(job),
        "rows": [
            {
                "row_index": r.row_index,
                "parsed_name": r.parsed_name,
                "parsed_sku": r.parsed_sku,
                "match_status": r.match_status,
                "decision": r.decision,
                "apply_result": r.apply_result,
                "apply_error": r.apply_error,
            }
            for r in rows
        ],
    }
