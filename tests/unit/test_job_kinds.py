"""Every declared job kind must have a handler (`ARCHITECTURE.md §7`).

A `kind` with no registered handler does not fail loudly — the runner marks the job
`dead` with "No handler registered" and moves on. That is correct behaviour for an
unknown kind arriving from an older deploy, and exactly the wrong way to discover that a
*scheduled* job has never run. So the registry is checked here instead, where it fails
the build.

The second test is the one that would actually catch a mistake: `JobKind` and `HANDLERS`
agreeing in both directions means a kind cannot be added without a handler, and a
handler cannot be registered under a string that no enum member spells.
"""

from __future__ import annotations

import bm.domain.jobs.handlers as handlers_module
from bm.infra.db.enums import JobKind
from bm.infra.queue.runner import HANDLERS


def test_every_job_kind_has_a_registered_handler() -> None:
    missing = [kind.value for kind in JobKind if kind.value not in HANDLERS]
    assert missing == [], (
        f"these job kinds would dead-letter with 'No handler registered': {missing}"
    )


def test_no_handler_is_registered_under_a_string_that_is_not_a_job_kind() -> None:
    """A typo in a registration key is otherwise invisible: the handler exists, the
    enqueue uses the enum, and the two never meet."""
    known = {kind.value for kind in JobKind}
    unexpected = sorted(set(handlers_module.REGISTRY_KEYS) - known)
    assert unexpected == []


def test_the_registry_covers_the_documented_set_exactly() -> None:
    """`ARCHITECTURE.md §7` lists seven jobs. Five are implemented; the other two are
    deliberately absent and the reason is recorded next to the enum:

    * `import.apply` is a synchronous endpoint, because FR-IMP-008 requires one
      transaction and the API contract requires an `Idempotency-Key` — both describe a
      request the caller waits on.
    * `credit.ageing_snapshot` needs a snapshot table `DATA-MODEL.md` never specifies.

    This test pins that decision so the absence stays deliberate rather than becoming
    something nobody remembers.
    """
    assert {kind.value for kind in JobKind} == {
        "import.parse",
        "inventory.reconcile",
        "idempotency.prune",
        "drafts.mark_stale",
        "notifications.dispatch",
    }


def test_handlers_accept_a_payload_dict_and_return_none() -> None:
    """The runner calls `handler(payload or {})` and ignores the return value, so a
    handler taking a different shape would fail only at delivery time."""
    import inspect

    for kind in JobKind:
        handler = HANDLERS[kind.value]
        signature = inspect.signature(handler)
        assert len(signature.parameters) == 1, f"{kind.value} takes the wrong arity"
