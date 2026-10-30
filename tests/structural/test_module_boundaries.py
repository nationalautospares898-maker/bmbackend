"""Structural rules that import-linter cannot express precisely.

The one that matters most: **a domain module may call another domain module only through
its `service.py`** (`ARCHITECTURE.md §3` rule 2). Reaching into another module's
repository or ORM model is how a modular monolith quietly becomes a tangle, and it is
also how a second writer appears for a table that is supposed to have exactly one.

Import-linter has no "except the module's own package" semantics, so this is checked by
walking the AST, which lets a module import its own internals freely while refusing to
let it import someone else's.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "bm"

#: Internal submodule names that belong to one domain module alone.
PRIVATE_SUBMODULES = ("repository", "models", "rules")


def _python_files(root: Path) -> list[Path]:
    return sorted(p for p in root.rglob("*.py") if "__pycache__" not in p.parts)


def _module_name(path: Path) -> str:
    relative = path.relative_to(SRC.parent).with_suffix("")
    parts = list(relative.parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _imported_modules(path: Path) -> list[str]:
    """Absolute `bm.*` module names imported by a file, including `from` targets."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names if alias.name.startswith("bm."))
        elif isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("bm."):
            found.append(node.module)
            # `from bm.domain.catalog import repository` imports a submodule, so the
            # names matter too.
            found.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_domain_modules_talk_only_through_each_other_s_service() -> None:
    violations: list[str] = []
    domain_root = SRC / "domain"

    for path in _python_files(domain_root):
        importer = _module_name(path)
        parts = importer.split(".")
        # bm.domain.<module>...
        own_module = parts[2] if len(parts) > 2 else None

        for imported in _imported_modules(path):
            target = imported.split(".")
            if len(target) < 4 or target[1] != "domain":
                continue
            other_module = target[2]
            if other_module in (own_module, "shared"):
                continue  # own internals, and shared primitives, are always allowed
            leaf = target[3]
            if leaf in PRIVATE_SUBMODULES:
                violations.append(
                    f"{importer} imports {imported} — cross-module calls go through "
                    f"bm.domain.{other_module}.service only"
                )

    assert violations == [], "\n".join(violations)


def test_api_layer_contains_no_money_arithmetic() -> None:
    """`CLAUDE.md §4`: `api/` has zero business logic — no money arithmetic.

    Decimal arithmetic in a router is how a total starts disagreeing with the invoice it
    is supposed to describe. `Decimal` may be *mentioned* for parsing and validation;
    what is forbidden is arithmetic on it.
    """
    violations: list[str] = []
    for path in _python_files(SRC / "api"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.BinOp):
                continue
            if not isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)):
                continue
            source = ast.unparse(node)
            if any(
                hint in source
                for hint in ("total", "amount", "price", "subtotal", "discount", "quantity")
            ):
                violations.append(f"{_module_name(path)}:{node.lineno} — {source}")

    assert violations == [], (
        "Money arithmetic belongs in bm.domain.shared.money, not in the api layer:\n"
        + "\n".join(violations)
    )


def test_no_float_conversion_in_the_domain_layer() -> None:
    """ADR-013. `float(` on a money value silently loses precision and there is no way
    to notice afterwards, so the call is forbidden outright in `domain/`."""
    violations: list[str] = []
    for path in _python_files(SRC / "domain"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "float"
            ):
                violations.append(f"{_module_name(path)}:{node.lineno}")
    assert violations == [], (
        "float() must not appear in domain/ (ADR-013): " + ", ".join(violations)
    )


def test_no_raw_sql_string_building() -> None:
    """T-8 / `SECURITY.md §4.1`. An f-string inside `text()` is the shape SQL injection
    takes; bound parameters are the only permitted form.

    Identifiers interpolated into DDL in a migration are a different case and migrations
    are not scanned here — they take no user input.
    """
    violations: list[str] = []
    for path in _python_files(SRC):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "text"
            ):
                continue
            if not node.args:
                continue
            argument = node.args[0]
            if isinstance(argument, ast.JoinedStr):
                violations.append(f"{_module_name(path)}:{node.lineno} — f-string in text()")
            elif isinstance(argument, ast.BinOp) and isinstance(argument.op, ast.Mod):
                violations.append(f"{_module_name(path)}:{node.lineno} — %-format in text()")
    assert violations == [], "\n".join(violations)


@pytest.mark.parametrize(
    "layer,forbidden",
    [("infra", "bm.domain"), ("infra", "bm.api"), ("domain", "bm.api")],
)
def test_no_reverse_imports(layer: str, forbidden: str) -> None:
    """Duplicates an import-linter contract on purpose: the linter can be skipped
    locally, and this is the rule whose violation is hardest to unwind later."""
    violations = [
        f"{_module_name(path)} imports {imported}"
        for path in _python_files(SRC / layer)
        for imported in _imported_modules(path)
        if imported == forbidden or imported.startswith(f"{forbidden}.")
    ]
    assert violations == [], "\n".join(violations)
