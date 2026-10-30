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
_ROUTER_MODULES: tuple[str, ...] = ()
