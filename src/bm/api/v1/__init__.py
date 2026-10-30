"""Version 1 router assembly.

Every router is mounted here and nowhere else, so the structural route-permission test
and the generated OpenAPI document have a single source of truth for the route table.
"""

from __future__ import annotations

from fastapi import APIRouter


def build_v1_router() -> APIRouter:
    router = APIRouter()
    for module_name in _ROUTER_MODULES:
        module = __import__(f"bm.api.v1.{module_name}", fromlist=["router"])
        router.include_router(module.router)
    return router


#: Mounted in this order. A milestone adds its module name here when its router lands.
_ROUTER_MODULES: tuple[str, ...] = (
    "auth",  # M3
    "admin",  # M3 — users and devices
    "settings",  # M4 — business rules the owner can change without a deploy
    "catalog",  # M5 — products, aliases, search
    "inventory",  # M6 — balances, ledger, adjustments, reconciliation
    "customers",  # M7
    "drafts",  # M8
    "sales",  # M10 — the single finalisation endpoint, cash and credit
    "credit",  # M11 — payments, allocations, credit views
    "invoices",  # M12 — read, print payload, cancellation
    "reports",  # M13
    "sync",  # M14 — bounded offline scope; no financial operation type
    "purchases",  # M15 + M24 — suppliers, purchases, returns
)
