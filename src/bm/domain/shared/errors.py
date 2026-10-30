"""Typed domain errors and the complete v1 error-code registry.

`docs/API-SPECIFICATION.md §4.2` lists 53 codes and declares them additive-only.
`ErrorCode` below is that registry in executable form: a test asserts the two agree,
so a code can never be emitted without being documented, nor documented without being
emittable. The frontend generates its exhaustive `ErrorCode` union from the same source
via OpenAPI, which is why the registry has to be a real enum rather than loose strings.

Clients branch on `code`, never on `message` (`CLAUDE.md §6`).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any


class ErrorCode(StrEnum):
    """Stable SCREAMING_SNAKE_CASE codes. Additive-only."""

    # --- authentication and identity -----------------------------------------
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    ACCOUNT_LOCKED = "ACCOUNT_LOCKED"
    USER_INACTIVE = "USER_INACTIVE"
    TOKEN_EXPIRED = "TOKEN_EXPIRED"
    REFRESH_TOKEN_REUSED = "REFRESH_TOKEN_REUSED"
    DEVICE_REVOKED = "DEVICE_REVOKED"
    DEVICE_NOT_REGISTERED = "DEVICE_NOT_REGISTERED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    BRANCH_SCOPE_INVALID = "BRANCH_SCOPE_INVALID"
    RATE_LIMITED = "RATE_LIMITED"

    # --- generic -------------------------------------------------------------
    VALIDATION_ERROR = "VALIDATION_ERROR"
    NOT_FOUND = "NOT_FOUND"

    # --- drafts --------------------------------------------------------------
    DRAFT_VERSION_CONFLICT = "DRAFT_VERSION_CONFLICT"
    DRAFT_LIMIT_REACHED = "DRAFT_LIMIT_REACHED"
    DRAFT_ALREADY_FINALIZED = "DRAFT_ALREADY_FINALIZED"
    DRAFT_OWNED_BY_OTHER_COUNTER = "DRAFT_OWNED_BY_OTHER_COUNTER"

    # --- stock and pricing ---------------------------------------------------
    INSUFFICIENT_STOCK = "INSUFFICIENT_STOCK"
    OVERSELL_NOT_PERMITTED = "OVERSELL_NOT_PERMITTED"
    OVERSELL_REASON_REQUIRED = "OVERSELL_REASON_REQUIRED"
    PRICE_CHANGED = "PRICE_CHANGED"
    PRICE_OVERRIDE_NOT_PERMITTED = "PRICE_OVERRIDE_NOT_PERMITTED"
    DISCOUNT_EXCEEDS_LIMIT = "DISCOUNT_EXCEEDS_LIMIT"
    PRODUCT_INACTIVE = "PRODUCT_INACTIVE"
    TOTAL_MISMATCH = "TOTAL_MISMATCH"
    EMPTY_SALE = "EMPTY_SALE"

    # --- credit --------------------------------------------------------------
    CREDIT_CUSTOMER_REQUIRED = "CREDIT_CUSTOMER_REQUIRED"
    CREDIT_LIMIT_EXCEEDED = "CREDIT_LIMIT_EXCEEDED"

    # --- idempotency ---------------------------------------------------------
    IDEMPOTENCY_KEY_REUSE = "IDEMPOTENCY_KEY_REUSE"
    IDEMPOTENT_REQUEST_IN_PROGRESS = "IDEMPOTENT_REQUEST_IN_PROGRESS"

    # --- invoice lifecycle ---------------------------------------------------
    INVOICE_ALREADY_CANCELLED = "INVOICE_ALREADY_CANCELLED"
    CANCEL_WINDOW_EXPIRED = "CANCEL_WINDOW_EXPIRED"
    INVOICE_CANCELLED = "INVOICE_CANCELLED"

    # --- payments and returns ------------------------------------------------
    OVER_ALLOCATION = "OVER_ALLOCATION"
    PAYMENT_ALREADY_REVERSED = "PAYMENT_ALREADY_REVERSED"
    RETURN_QUANTITY_EXCEEDED = "RETURN_QUANTITY_EXCEEDED"
    RETURN_WINDOW_EXPIRED = "RETURN_WINDOW_EXPIRED"

    # --- duplicates ----------------------------------------------------------
    DUPLICATE_SKU = "DUPLICATE_SKU"
    DUPLICATE_BARCODE = "DUPLICATE_BARCODE"
    DUPLICATE_SUPPLIER_DOCUMENT = "DUPLICATE_SUPPLIER_DOCUMENT"
    DUPLICATE_FILE = "DUPLICATE_FILE"

    # --- imports -------------------------------------------------------------
    IMPORT_ROWS_PENDING_DECISION = "IMPORT_ROWS_PENDING_DECISION"
    IMPORT_ALREADY_APPLIED = "IMPORT_ALREADY_APPLIED"
    UNSUPPORTED_FILE_TYPE = "UNSUPPORTED_FILE_TYPE"
    FILE_TOO_LARGE = "FILE_TOO_LARGE"

    # --- tax -----------------------------------------------------------------
    TAX_CONFIG_INCOMPLETE = "TAX_CONFIG_INCOMPLETE"

    # --- sync ----------------------------------------------------------------
    SYNC_CURSOR_EXPIRED = "SYNC_CURSOR_EXPIRED"
    SYNC_BATCH_TOO_LARGE = "SYNC_BATCH_TOO_LARGE"
    PRECONDITION_REQUIRED = "PRECONDITION_REQUIRED"
    SKIPPED_AFTER_EARLIER_FAILURE = "SKIPPED_AFTER_EARLIER_FAILURE"
    CORRUPT_VERSION = "CORRUPT_VERSION"
    DRAFT_ALREADY_CANCELLED = "DRAFT_ALREADY_CANCELLED"

    # --- infrastructure ------------------------------------------------------
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class WarningCode(StrEnum):
    """Rides along with a successful response. Never a reason to retry (§4.3)."""

    LOW_STOCK_AFTER_SALE = "LOW_STOCK_AFTER_SALE"
    CREDIT_LIMIT_APPROACHED = "CREDIT_LIMIT_APPROACHED"
    CUSTOMER_DUPLICATE_SUSPECTED = "CUSTOMER_DUPLICATE_SUSPECTED"
    CATALOG_CACHE_TRUNCATED = "CATALOG_CACHE_TRUNCATED"


class DomainError(Exception):
    """Base class for every expected business failure.

    A single handler in `api/errors.py` turns these into the documented envelope, so a
    service never constructs an HTTP response and a router never interprets a rule.
    """

    code: ErrorCode = ErrorCode.INTERNAL_ERROR
    status_code: int = 400
    retryable: bool = False
    message: str = "Request could not be completed."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: dict[str, Any] | None = None,
    ) -> None:
        self.message = message or self.message
        self.details = details or {}
        super().__init__(self.message)


# --- 401 / 403 / 423 ----------------------------------------------------------


class InvalidCredentials(DomainError):
    code = ErrorCode.INVALID_CREDENTIALS
    status_code = 401
    message = "Username or password is incorrect."


class TokenExpired(DomainError):
    code = ErrorCode.TOKEN_EXPIRED
    status_code = 401
    message = "Your session has expired. Please sign in again."


class RefreshTokenReused(DomainError):
    code = ErrorCode.REFRESH_TOKEN_REUSED
    status_code = 401
    message = "This session was signed out for safety. Please sign in again."


class AccountLocked(DomainError):
    code = ErrorCode.ACCOUNT_LOCKED
    status_code = 423
    message = "Too many failed attempts. Try again shortly."


class UserInactive(DomainError):
    code = ErrorCode.USER_INACTIVE
    status_code = 403
    message = "This account is no longer active."


class DeviceRevoked(DomainError):
    code = ErrorCode.DEVICE_REVOKED
    status_code = 403
    message = "This device is no longer allowed to use the system."


class DeviceNotRegistered(DomainError):
    code = ErrorCode.DEVICE_NOT_REGISTERED
    status_code = 403
    message = "This device is not registered."


class PermissionDenied(DomainError):
    code = ErrorCode.PERMISSION_DENIED
    status_code = 403
    message = "You do not have permission to do this."

    def __init__(self, required_permission: str, message: str | None = None) -> None:
        super().__init__(message, details={"required_permission": required_permission})


class BranchScopeInvalid(DomainError):
    code = ErrorCode.BRANCH_SCOPE_INVALID
    status_code = 403
    message = "You are not assigned to that branch."


class RateLimited(DomainError):
    code = ErrorCode.RATE_LIMITED
    status_code = 429
    retryable = True
    message = "Too many requests. Please wait a moment."


# --- 404 / 422 ----------------------------------------------------------------


class NotFound(DomainError):
    """Also used for cross-tenant access, so existence is never leaked (§4.1)."""

    code = ErrorCode.NOT_FOUND
    status_code = 404
    message = "Not found."


class ValidationFailed(DomainError):
    code = ErrorCode.VALIDATION_ERROR
    status_code = 422
    message = "Some values need correcting."

    def __init__(self, fields: dict[str, str], message: str | None = None) -> None:
        super().__init__(message, details={"fields": fields})


class PreconditionRequired(DomainError):
    code = ErrorCode.PRECONDITION_REQUIRED
    status_code = 428
    message = "This bill may be open elsewhere. Reload it before saving."


# --- drafts -------------------------------------------------------------------


class DraftVersionConflict(DomainError):
    code = ErrorCode.DRAFT_VERSION_CONFLICT
    status_code = 409
    message = "This bill was changed somewhere else."


class DraftLimitReached(DomainError):
    code = ErrorCode.DRAFT_LIMIT_REACHED
    status_code = 409
    message = "All bill tabs are in use. Complete or clear one first."


class DraftAlreadyFinalized(DomainError):
    code = ErrorCode.DRAFT_ALREADY_FINALIZED
    status_code = 409
    message = "This bill was already completed."


class DraftAlreadyCancelled(DomainError):
    code = ErrorCode.DRAFT_ALREADY_CANCELLED
    status_code = 409
    message = "This bill was already cleared."


class DraftOwnedByOtherCounter(DomainError):
    code = ErrorCode.DRAFT_OWNED_BY_OTHER_COUNTER
    status_code = 403
    message = "This bill belongs to another counter."


# --- stock and pricing --------------------------------------------------------


class InsufficientStock(DomainError):
    code = ErrorCode.INSUFFICIENT_STOCK
    status_code = 409
    message = "Not enough stock."


class OversellNotPermitted(DomainError):
    code = ErrorCode.OVERSELL_NOT_PERMITTED
    status_code = 403
    message = "Selling below available stock needs a supervisor."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, details={"required_permission": "inventory.oversell.override"})


class OversellReasonRequired(DomainError):
    code = ErrorCode.OVERSELL_REASON_REQUIRED
    status_code = 422
    message = "A reason of at least 10 characters is required to sell below stock."


class PriceChanged(DomainError):
    code = ErrorCode.PRICE_CHANGED
    status_code = 409
    message = "Prices changed since this bill was started."


class PriceOverrideNotPermitted(DomainError):
    code = ErrorCode.PRICE_OVERRIDE_NOT_PERMITTED
    status_code = 403
    message = "Changing a price needs a supervisor."

    def __init__(self, message: str | None = None) -> None:
        super().__init__(message, details={"required_permission": "billing.price.override"})


class DiscountExceedsLimit(DomainError):
    code = ErrorCode.DISCOUNT_EXCEEDS_LIMIT
    status_code = 409
    message = "This discount is above your limit."


class ProductInactive(DomainError):
    code = ErrorCode.PRODUCT_INACTIVE
    status_code = 409
    message = "An item on this bill is no longer stocked."


class TotalMismatch(DomainError):
    """The client's cross-check disagreed. Never silently overridden (FR-SALE-003)."""

    code = ErrorCode.TOTAL_MISMATCH
    status_code = 422
    message = "The total on screen does not match the server. Reload the bill."


class EmptySale(DomainError):
    code = ErrorCode.EMPTY_SALE
    status_code = 422
    message = "Add at least one item before completing the bill."


# --- credit -------------------------------------------------------------------


class CreditCustomerRequired(DomainError):
    code = ErrorCode.CREDIT_CUSTOMER_REQUIRED
    status_code = 409
    message = "A credit bill needs a saved customer, not just a typed name."


class CreditLimitExceeded(DomainError):
    code = ErrorCode.CREDIT_LIMIT_EXCEEDED
    status_code = 409
    message = "This sale would take the customer past their credit limit."


# --- idempotency --------------------------------------------------------------


class IdempotencyKeyReuse(DomainError):
    code = ErrorCode.IDEMPOTENCY_KEY_REUSE
    status_code = 409
    message = "This request id was already used for a different bill."


class IdempotentRequestInProgress(DomainError):
    code = ErrorCode.IDEMPOTENT_REQUEST_IN_PROGRESS
    status_code = 409
    retryable = True
    message = "This bill is still being completed. Please wait."

    def __init__(self, retry_after_ms: int = 500) -> None:
        super().__init__(details={"retry_after_ms": retry_after_ms})


# --- invoice lifecycle --------------------------------------------------------


class InvoiceAlreadyCancelled(DomainError):
    code = ErrorCode.INVOICE_ALREADY_CANCELLED
    status_code = 409
    message = "This bill was already cancelled."


class CancelWindowExpired(DomainError):
    code = ErrorCode.CANCEL_WINDOW_EXPIRED
    status_code = 409
    message = "This bill is too old to cancel. Use a return instead."


class InvoiceCancelled(DomainError):
    code = ErrorCode.INVOICE_CANCELLED
    status_code = 409
    message = "This bill was cancelled."


# --- payments and returns -----------------------------------------------------


class OverAllocation(DomainError):
    code = ErrorCode.OVER_ALLOCATION
    status_code = 422
    message = "That would pay more than the bill's outstanding amount."


class PaymentAlreadyReversed(DomainError):
    code = ErrorCode.PAYMENT_ALREADY_REVERSED
    status_code = 409
    message = "This payment was already reversed."


class ReturnQuantityExceeded(DomainError):
    code = ErrorCode.RETURN_QUANTITY_EXCEEDED
    status_code = 409
    message = "That is more than was sold."


class ReturnWindowExpired(DomainError):
    code = ErrorCode.RETURN_WINDOW_EXPIRED
    status_code = 409
    message = "This bill is outside the return window."


# --- duplicates ---------------------------------------------------------------


class DuplicateSku(DomainError):
    code = ErrorCode.DUPLICATE_SKU
    status_code = 409
    message = "Another product already uses this part code."


class DuplicateBarcode(DomainError):
    code = ErrorCode.DUPLICATE_BARCODE
    status_code = 409
    message = "Another product already uses this barcode."


class DuplicateSupplierDocument(DomainError):
    code = ErrorCode.DUPLICATE_SUPPLIER_DOCUMENT
    status_code = 409
    message = "This supplier bill was already received."


class DuplicateFile(DomainError):
    code = ErrorCode.DUPLICATE_FILE
    status_code = 409
    message = "This exact file was already uploaded."


# --- imports ------------------------------------------------------------------


class ImportRowsPendingDecision(DomainError):
    code = ErrorCode.IMPORT_ROWS_PENDING_DECISION
    status_code = 409
    message = "Some rows still need a decision before this can be applied."


class ImportAlreadyApplied(DomainError):
    code = ErrorCode.IMPORT_ALREADY_APPLIED
    status_code = 409
    message = "This import was already applied."


class UnsupportedFileType(DomainError):
    code = ErrorCode.UNSUPPORTED_FILE_TYPE
    status_code = 415
    message = "Only .xlsx, .csv and .pdf files can be uploaded."


class FileTooLarge(DomainError):
    code = ErrorCode.FILE_TOO_LARGE
    status_code = 413
    message = "This file is too large."


# --- tax ----------------------------------------------------------------------


class TaxConfigIncomplete(DomainError):
    code = ErrorCode.TAX_CONFIG_INCOMPLETE
    status_code = 422
    message = "Tax settings are incomplete."


# --- sync ---------------------------------------------------------------------


class SyncCursorExpired(DomainError):
    code = ErrorCode.SYNC_CURSOR_EXPIRED
    status_code = 409
    message = "The offline catalog is too old and must be rebuilt."


class SyncBatchTooLarge(DomainError):
    code = ErrorCode.SYNC_BATCH_TOO_LARGE
    status_code = 422
    message = "Too many changes in one sync batch."


class CorruptVersion(DomainError):
    code = ErrorCode.CORRUPT_VERSION
    status_code = 409
    message = "This bill's local copy is ahead of the server, which should not happen."


class SkippedAfterEarlierFailure(DomainError):
    code = ErrorCode.SKIPPED_AFTER_EARLIER_FAILURE
    status_code = 409
    message = "Skipped because an earlier change to the same bill needs attention."


# --- infrastructure -----------------------------------------------------------


class ServiceUnavailable(DomainError):
    code = ErrorCode.SERVICE_UNAVAILABLE
    status_code = 503
    retryable = True
    message = "The service is briefly unavailable. Your bill is safe."

    def __init__(self, retry_after_ms: int = 2000) -> None:
        super().__init__(details={"retry_after_ms": retry_after_ms})
