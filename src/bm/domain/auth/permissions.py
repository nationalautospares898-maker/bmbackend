"""The permission vocabulary and the role mapping.

`Permission` is the closed set of permission strings from `docs/PRD.md §3.2`. Using a
string that is not in this set is a typo that grants nothing — the worst kind of
authorisation bug — so the set is an enum, the seed migration reads it, and
`ROLE_PERMISSIONS` below is a direct transcription of that document's matrix.

This lives in `domain` rather than `api` because services re-assert permissions
(`SECURITY.md §3.1` rule 2) and `domain` may not import `api`. How a *route* declares
its permission is in `bm/api/deps.py`; which routes are deliberately public is in
`bm/api/route_policy.py`.

⚠️ Do not confuse these strings with the audit-action or settings-key spellings of the
same concept. `docs/DATA-MODEL.md §3.14` is the authoritative table for all five
oversell vocabularies.
"""

from __future__ import annotations

from enum import StrEnum


class Permission(StrEnum):
    """Closed set. Adding one requires updating `PRD.md §3.2` and the role mapping."""

    BILLING_DRAFT_MANAGE = "billing.draft.manage"
    BILLING_SALE_FINALIZE = "billing.sale.finalize"
    BILLING_PRICE_OVERRIDE = "billing.price.override"
    BILLING_DISCOUNT_APPLY = "billing.discount.apply"
    BILLING_SALE_CANCEL = "billing.sale.cancel"
    BILLING_DRAFT_TAKEOVER = "billing.draft.takeover"

    INVENTORY_OVERSELL_OVERRIDE = "inventory.oversell.override"
    INVENTORY_ADJUST = "inventory.adjust"
    INVENTORY_RECEIVE = "inventory.receive"

    CATALOG_READ = "catalog.read"
    CATALOG_WRITE = "catalog.write"
    CATALOG_COST_READ = "catalog.cost.read"

    CUSTOMER_MANAGE = "customer.manage"

    CREDIT_READ = "credit.read"
    CREDIT_PAYMENT_RECORD = "credit.payment.record"
    CREDIT_WRITEOFF = "credit.writeoff"

    RETURNS_CREATE = "returns.create"
    IMPORTS_MANAGE = "imports.manage"

    REPORTS_SALES_READ = "reports.sales.read"
    REPORTS_FINANCIAL_READ = "reports.financial.read"

    ADMIN_USERS_MANAGE = "admin.users.manage"
    ADMIN_SETTINGS_MANAGE = "admin.settings.manage"
    ADMIN_DEVICES_MANAGE = "admin.devices.manage"

    AUDIT_READ = "audit.read"


#: Human descriptions, seeded into `permissions.description` by migration 0016.
PERMISSION_DESCRIPTIONS: dict[Permission, str] = {
    Permission.BILLING_DRAFT_MANAGE: "Create and edit draft bills",
    Permission.BILLING_SALE_FINALIZE: "Complete a cash or credit sale",
    Permission.BILLING_PRICE_OVERRIDE: "Change an item's price on a bill",
    Permission.BILLING_DISCOUNT_APPLY: "Apply a discount to a bill",
    Permission.BILLING_SALE_CANCEL: "Cancel a completed bill within the allowed window",
    Permission.BILLING_DRAFT_TAKEOVER: "Take over a draft bill from another counter",
    Permission.INVENTORY_OVERSELL_OVERRIDE: "Sell below the recorded stock, with a reason",
    Permission.INVENTORY_ADJUST: "Correct stock quantities, with a reason",
    Permission.INVENTORY_RECEIVE: "Receive supplier stock",
    Permission.CATALOG_READ: "View products and stock",
    Permission.CATALOG_WRITE: "Add and edit products",
    Permission.CATALOG_COST_READ: "View cost prices and margins",
    Permission.CUSTOMER_MANAGE: "Add and edit customers",
    Permission.CREDIT_READ: "View credit balances and payment history",
    Permission.CREDIT_PAYMENT_RECORD: "Record and reverse customer payments",
    Permission.CREDIT_WRITEOFF: "Write off an unrecoverable balance",
    Permission.RETURNS_CREATE: "Record a customer return",
    Permission.IMPORTS_MANAGE: "Upload and apply price lists and supplier bills",
    Permission.REPORTS_SALES_READ: "View sales reports and bill history",
    Permission.REPORTS_FINANCIAL_READ: "View financial and margin reports",
    Permission.ADMIN_USERS_MANAGE: "Manage users and passwords",
    Permission.ADMIN_SETTINGS_MANAGE: "Change business settings",
    Permission.ADMIN_DEVICES_MANAGE: "Register and revoke devices",
    Permission.AUDIT_READ: "View the audit log and reconciliation reports",
}


class SystemRole(StrEnum):
    OWNER = "owner"
    MANAGER = "manager"
    CASHIER = "cashier"
    STOCK_KEEPER = "stock_keeper"
    ACCOUNTANT = "accountant"
    AUDITOR = "auditor"


ROLE_NAMES: dict[SystemRole, str] = {
    SystemRole.OWNER: "Owner",
    SystemRole.MANAGER: "Manager",
    SystemRole.CASHIER: "Cashier",
    SystemRole.STOCK_KEEPER: "Stock keeper",
    SystemRole.ACCOUNTANT: "Accountant",
    SystemRole.AUDITOR: "Auditor",
}

P = Permission

#: Role → permissions, transcribed from the matrix in `docs/PRD.md §3.2`.
#:
#: Two cells in that matrix are not plain ticks and are handled outside this map:
#:   * Cashier `billing.discount.apply` is "limited" — the permission is granted and the
#:     cap is enforced from `settings.discount.cashier_max_percent` (default 0).
#:   * Cashier `reports.sales.read` is "own" — granted, and the report service narrows
#:     the result to the caller's own counter/shift.
#: `Auditor` is read-only at the API level: every write endpoint rejects the role because
#: no write permission appears in its set.
ROLE_PERMISSIONS: dict[SystemRole, frozenset[Permission]] = {
    SystemRole.OWNER: frozenset(Permission),
    SystemRole.MANAGER: frozenset(
        {
            P.BILLING_DRAFT_MANAGE,
            P.BILLING_SALE_FINALIZE,
            P.BILLING_PRICE_OVERRIDE,
            P.BILLING_DISCOUNT_APPLY,
            P.BILLING_SALE_CANCEL,
            P.BILLING_DRAFT_TAKEOVER,
            P.INVENTORY_OVERSELL_OVERRIDE,
            P.INVENTORY_ADJUST,
            P.INVENTORY_RECEIVE,
            P.CATALOG_READ,
            P.CATALOG_WRITE,
            P.CATALOG_COST_READ,
            P.CUSTOMER_MANAGE,
            P.CREDIT_READ,
            P.CREDIT_PAYMENT_RECORD,
            P.RETURNS_CREATE,
            P.IMPORTS_MANAGE,
            P.REPORTS_SALES_READ,
            P.REPORTS_FINANCIAL_READ,
            P.ADMIN_DEVICES_MANAGE,
            P.AUDIT_READ,
        }
    ),
    SystemRole.CASHIER: frozenset(
        {
            P.BILLING_DRAFT_MANAGE,
            P.BILLING_SALE_FINALIZE,
            P.BILLING_DISCOUNT_APPLY,  # capped by settings
            P.CATALOG_READ,
            P.CUSTOMER_MANAGE,
            P.CREDIT_READ,
            P.CREDIT_PAYMENT_RECORD,
            P.REPORTS_SALES_READ,  # narrowed to own counter
        }
    ),
    SystemRole.STOCK_KEEPER: frozenset(
        {
            P.INVENTORY_ADJUST,
            P.INVENTORY_RECEIVE,
            P.CATALOG_READ,
            P.CATALOG_WRITE,
            P.CATALOG_COST_READ,
            P.IMPORTS_MANAGE,
        }
    ),
    SystemRole.ACCOUNTANT: frozenset(
        {
            P.CATALOG_READ,
            P.CATALOG_COST_READ,
            P.CREDIT_READ,
            P.CREDIT_PAYMENT_RECORD,
            P.REPORTS_SALES_READ,
            P.REPORTS_FINANCIAL_READ,
            P.AUDIT_READ,
        }
    ),
    SystemRole.AUDITOR: frozenset(
        {
            P.CATALOG_READ,
            P.CATALOG_COST_READ,
            P.CREDIT_READ,
            P.REPORTS_SALES_READ,
            P.REPORTS_FINANCIAL_READ,
            P.AUDIT_READ,
        }
    ),
}


def permissions_for_role(role: SystemRole | str) -> frozenset[Permission]:
    """Resolve a system role to its permission set.

    Custom (non-system) roles are resolved from `role_permissions` rows instead; this
    function covers the seeded roles only.
    """
    try:
        key = SystemRole(role)
    except ValueError:
        return frozenset()
    return ROLE_PERMISSIONS[key]
