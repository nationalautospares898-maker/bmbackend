"""Every ORM mapper, imported so Alembic's autogenerate sees the full metadata.

ORM models live here and never leave a repository as an API response type
(`ARCHITECTURE.md §3` rule 4). Services exchange domain dataclasses instead, which is
what keeps a database column rename from becoming an API change.
"""

from __future__ import annotations

from bm.infra.db.models.catalog import (
    Brand,
    Category,
    Product,
    ProductAlias,
    ProductPrice,
    TaxConfigVersion,
    TaxGroup,
    TaxRate,
    Unit,
)
from bm.infra.db.models.credit import (
    CreditNote,
    CreditNoteAllocation,
    Payment,
    PaymentAllocation,
    Purchase,
    PurchaseItem,
    SalesReturn,
    SalesReturnItem,
    Supplier,
)
from bm.infra.db.models.customers import (
    Customer,
    CustomerCreditBalance,
    CustomerPhone,
    CustomerVehicle,
)
from bm.infra.db.models.identity import (
    AuditLog,
    Device,
    LoginAttempt,
    Membership,
    Permission,
    RefreshToken,
    Role,
    RolePermission,
    User,
)
from bm.infra.db.models.infra import (
    IdempotencyKey,
    ImportJob,
    ImportRow,
    Job,
    Notification,
    OutboxEvent,
    SyncOperation,
)
from bm.infra.db.models.inventory import (
    InventoryBalance,
    ReconciliationRun,
    StockCountItem,
    StockCountSession,
    StockMovement,
)
from bm.infra.db.models.sales import (
    DraftBill,
    DraftBillItem,
    Invoice,
    InvoiceItem,
    InvoiceSeries,
    PrintLog,
)
from bm.infra.db.models.tenancy import Branch, Business, Counter, Setting

__all__ = [
    "AuditLog",
    "Branch",
    "Brand",
    "Business",
    "Category",
    "Counter",
    "CreditNote",
    "CreditNoteAllocation",
    "Customer",
    "CustomerCreditBalance",
    "CustomerPhone",
    "CustomerVehicle",
    "Device",
    "DraftBill",
    "DraftBillItem",
    "IdempotencyKey",
    "ImportJob",
    "ImportRow",
    "InventoryBalance",
    "Invoice",
    "InvoiceItem",
    "InvoiceSeries",
    "Job",
    "LoginAttempt",
    "Membership",
    "Notification",
    "OutboxEvent",
    "Payment",
    "PaymentAllocation",
    "Permission",
    "PrintLog",
    "Product",
    "ProductAlias",
    "ProductPrice",
    "Purchase",
    "PurchaseItem",
    "ReconciliationRun",
    "RefreshToken",
    "Role",
    "RolePermission",
    "SalesReturn",
    "SalesReturnItem",
    "Setting",
    "StockCountItem",
    "StockCountSession",
    "StockMovement",
    "Supplier",
    "SyncOperation",
    "TaxConfigVersion",
    "TaxGroup",
    "TaxRate",
    "Unit",
    "User",
]
