"""The production database workflow: inspect, migrate, verify, provision.

One file, four subcommands, one guard shared by all of them. It exists because every
command that touches a production database needs the *same* three things to be true
before it runs, and remembering them per command is how the wrong database gets written
to:

    1. the connection string is real, not a placeholder left in `.env.production`;
    2. the host is the intended Neon endpoint, not `localhost`;
    3. the database name is the intended one.

`_guard_target()` checks all three against the URL that is actually about to be used, and
every subcommand calls it. Nothing here reads `.env` — the variables come from the
process environment, which `scripts/prod.ps1` loads from `.env.production`.

    ./scripts/prod.ps1 python scripts/production_db.py inspect
    ./scripts/prod.ps1 python scripts/production_db.py migrate --yes
    ./scripts/prod.ps1 python scripts/production_db.py verify
    ./scripts/prod.ps1 python scripts/production_db.py provision --dry-run


WHAT IT WILL NOT DO
-------------------
There is no `reset`, no `drop`, no `truncate` and no `downgrade` subcommand, and that is
a design decision rather than an omission. Three rules from `CLAUDE.md §5` and §7 make
every one of them wrong against production:

  * finalised financial records are immutable, and the database privileges say so;
  * a destructive migration is recovered by PITR or a tested `alembic downgrade -1` run
    deliberately, not by a convenience wrapper;
  * dropping objects to force a clean migration destroys the audit trail, which is the
    one thing a billing system cannot rebuild.

`migrate` also refuses to touch a database that already holds business data or that has
tables without an `alembic_version` row. It reports and stops; it never adapts.

Seeding is likewise bounded. `provision` writes exactly the five rows that make the
application reachable — a business, a branch, a counter, an owner user, a membership —
and nothing else. It cannot create a product, a customer, stock, an invoice, a sale or a
payment, because it contains no statement that writes to those tables. Invoice series are
created lazily inside the first finalisation transaction (`domain/sales/numbering.py`),
and `settings` defaults live in code, so a correct production database starts with zero
rows in both.
"""

from __future__ import annotations

import argparse
import os
import re
import secrets
import sys
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from bm.infra.db import models as _models  # noqa: F401  (registers every mapper)
from bm.infra.db.base import Base
from bm.infra.db.enums import ALL_ENUM_TYPES
from bm.infra.db.migration_state import expected_head

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The three extensions migration `0001` creates. The search design depends on all of
#: them: `pg_trgm` for the fuzzy tier, `unaccent` for normalisation, `citext` for
#: case-insensitive usernames.
REQUIRED_EXTENSIONS = ("pg_trgm", "unaccent", "citext")

#: Tables that may legitimately hold rows in a correct, empty production database.
#:
#:   * `permissions`, `roles`, `role_permissions` — migration `0005`.
#:   * `units` — migration `0016`.
#:   * `businesses`, `branches`, `counters`, `users`, `memberships` — `provision`.
#:   * `settings` — owner-set business rules. A fresh database has **zero**: defaults
#:     live in `domain/settings/defaults.py` precisely so an unset key is never
#:     ambiguous (`DATA-MODEL.md §3.1`).
#:
#: Every other table is business data and must be empty. The business list is derived by
#: *subtraction* from `Base.metadata` rather than written out, so a table added by a
#: future migration is checked automatically. The fail-safe direction: a new table is
#: assumed to be business data until somebody deliberately adds it here.
CONFIG_TABLES = frozenset(
    {
        "alembic_version",
        "permissions",
        "roles",
        "role_permissions",
        "units",
        "businesses",
        "branches",
        "counters",
        "users",
        "memberships",
        "settings",
    }
)

#: Must match migration `0005`'s namespace, or a membership points at a role that does
#: not exist and the owner signs in with no permissions at all.
_ROLE_NAMESPACE = uuid.UUID("6f1c2d4e-0000-4000-8000-000000000000")

#: Must match migration `0016`'s namespace, so that re-running it against a database
#: provisioned here updates these unit rows rather than creating a second set.
_UNIT_NAMESPACE = uuid.UUID("7a2d3e5f-0000-4000-8000-000000000000")

# --- the owner's own letterhead ----------------------------------------------
#
# Read out of the owner's estimate workbook `ANAS_1234_20260606_231456.xlsx`, rows 1–3 of
# `Sheet1`, and identical to the constants in `scripts/set_business_identity.py` — which
# exists because `seed_dev.py` had written `DEMO — NOT A REAL BUSINESS` and a plausible
# placeholder in a seeded database is exactly how a placeholder reaches a customer's bill.
#
# Nothing here is invented. `legal_name` and `tax_identifier` stay NULL: the workbook
# shows a trading name, not a registered legal name, no GSTIN appears anywhere in it, and
# OD-4 is still open. Guessing either is the precise mistake these defaults avoid.
DEFAULT_DISPLAY_NAME = "NATIONAL AUTO SPARES"
DEFAULT_ADDRESS_LINES = ["AUTO NAGAR, HYDERABAD"]
DEFAULT_PHONE_NUMBERS = ["9948207903", "9848136564"]
DEFAULT_TIMEZONE = "Asia/Kolkata"
DEFAULT_FY_START_MONTH = 4


class Refused(SystemExit):
    """A guard said no. `SystemExit` so it exits non-zero without a traceback."""

    def __init__(self, message: str) -> None:
        super().__init__(f"\nREFUSED: {message}\n")


def _business_tables() -> list[str]:
    return sorted(set(Base.metadata.tables) - CONFIG_TABLES)


# --- target resolution and guards --------------------------------------------


@dataclass(frozen=True, slots=True)
class Target:
    url: str
    host: str
    database: str
    user: str

    @property
    def masked(self) -> str:
        """What is safe to print, and what an operator actually needs to see.

        The password is gone for the obvious reason. The Neon **endpoint id**
        (`ep-cold-pond-123456`) is gone for a less obvious one: it is not a credential by
        itself, but it is the routable address of the shop's production database, and this
        string is printed by every subcommand — so it ends up in terminal scrollback, in
        pasted output and in reports. Paired with a leaked password it is everything
        needed to connect.

        What survives is what the operator has to confirm before a write: the **role**,
        the **database name**, the **region** and whether the endpoint is **pooled**.
        """
        host = re.sub(r"ep-[a-z0-9]+-[a-z0-9]+-?[a-z0-9]*", "ep-<redacted>", self.host)
        return f"{self.user}:***@{host}/{self.database}"


_PLACEHOLDER_MARKERS = ("PASTE_", "REPLACE_", "CHANGE_ME", "REPLACE-ME")

#: The password placeholder in `scripts/neon-init-roles.sql`. Its continued presence in
#: the committed file is asserted before every run: if it is gone, somebody has pasted a
#: live credential into a tracked file.
_ROLE_SQL_PLACEHOLDER = "REPLACE_WITH_A_STRONG_GENERATED_PASSWORD"


def _resolve(variable: str) -> Target:
    url = os.environ.get(variable, "").strip()
    if not url:
        raise Refused(
            f"{variable} is not set.\n"
            "  Run through the wrapper so `.env.production` is loaded:\n"
            "    ./scripts/prod.ps1 python scripts/production_db.py <command>"
        )

    for marker in _PLACEHOLDER_MARKERS:
        if marker in url:
            raise Refused(
                f"{variable} still contains the placeholder {marker!r}.\n"
                "  Fill in the real Neon connection string in `.env.production` first.\n"
                "  See `.env.production.example` §2 for where to find it."
            )

    # `urlsplit` does not understand `postgresql+psycopg`, so the driver suffix is
    # removed for parsing only. The URL handed to SQLAlchemy is always the original.
    parts = urlsplit(url.replace("+psycopg", "", 1))
    if parts.scheme not in ("postgresql", "postgres"):
        raise Refused(f"{variable} is not a postgresql:// URL (got scheme {parts.scheme!r}).")

    return Target(
        url=url,
        host=parts.hostname or "",
        database=parts.path.lstrip("/"),
        user=parts.username or "",
    )


def _guard_target(target: Target, args: argparse.Namespace, *, variable: str) -> None:
    """Refuse anything that is not the intended production database.

    The checks are on the **resolved URL**, not on which variable it came from. A correct
    value in the wrong variable, or a local URL that survived a copy-paste, both land
    here. `tests/conftest.py` guards the opposite direction with the same shape of
    allow-list, and for the same reason: a refusal is the only outcome that cannot be
    mistaken for success.
    """
    local = {"localhost", "127.0.0.1", "::1", "0.0.0.0", ""}
    if target.host in local:
        raise Refused(
            f"{variable} points at {target.host or '(no host)'!r} — a local database.\n"
            "  This tool only ever talks to the production Neon endpoint. If you meant to\n"
            "  work locally, use `uv run alembic ...` with `.env`."
        )

    if target.database != args.expect_database:
        raise Refused(
            f"{variable} names database {target.database!r}, expected "
            f"{args.expect_database!r}.\n"
            "  Pass --expect-database if the production database really is called "
            "something else."
        )

    if not args.allow_any_host and not target.host.endswith(args.expect_host_suffix):
        raise Refused(
            f"{variable} host {target.host!r} does not end with "
            f"{args.expect_host_suffix!r}.\n"
            "  Neon endpoints look like "
            "`ep-xxxx-123456.ap-southeast-1.aws.neon.tech`.\n"
            "  Use --expect-host-suffix for a different provider, or --allow-any-host to\n"
            "  skip this check deliberately."
        )

    if args.allow_any_host:
        print("  ! --allow-any-host: the host check was skipped on purpose.")


def _is_pooled(target: Target) -> bool:
    """Neon's pooled endpoint carries `-pooler` in the hostname."""
    return "-pooler." in target.host


def _connect(target: Target) -> Connection:
    try:
        engine = create_engine(target.url, pool_pre_ping=True, future=True)
        return engine.connect()
    except SQLAlchemyError as exc:
        raise Refused(
            f"could not connect to {target.masked}:\n  {type(exc).__name__}: {exc}\n\n"
            "  Common causes: the password was rotated in the Neon console; `?sslmode=require`\n"
            "  is missing; the branch was deleted; the role does not exist yet (run\n"
            "  `scripts/neon-init-roles.sql` first)."
        ) from exc


# --- facts about the database ------------------------------------------------


@dataclass(frozen=True, slots=True)
class Facts:
    server_version: str
    current_database: str
    current_user: str
    extensions: dict[str, str]
    tables: list[str]
    enum_types: list[str]
    index_count: int
    constraint_counts: dict[str, int]
    alembic_revision: str | None
    app_role_exists: bool
    row_counts: dict[str, int]

    @property
    def is_untouched(self) -> bool:
        """No application tables at all — a branch nothing has ever been applied to."""
        return not self.tables and self.alembic_revision is None

    @property
    def business_rows(self) -> int:
        return sum(self.row_counts.values())


def _gather(connection: Connection, *, count_rows: bool) -> Facts:
    def scalar(sql: str) -> str:
        return str(connection.execute(text(sql)).scalar_one())

    extensions = {
        str(name): str(version)
        for name, version in connection.execute(
            text("SELECT extname, extversion FROM pg_extension ORDER BY extname")
        ).all()
    }

    tables = [
        str(row[0])
        for row in connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY tablename")
        ).all()
    ]

    enum_types = [
        str(row[0])
        for row in connection.execute(
            text(
                "SELECT t.typname FROM pg_type t"
                " JOIN pg_namespace n ON n.oid = t.typnamespace"
                " WHERE t.typtype = 'e' AND n.nspname = 'public' ORDER BY t.typname"
            )
        ).all()
    ]

    index_count = int(
        connection.execute(
            text("SELECT count(*) FROM pg_indexes WHERE schemaname = 'public'")
        ).scalar_one()
    )

    constraint_counts = {
        str(kind): int(total)
        for kind, total in connection.execute(
            text(
                "SELECT contype, count(*) FROM pg_constraint c"
                " JOIN pg_namespace n ON n.oid = c.connamespace"
                " WHERE n.nspname = 'public' GROUP BY contype ORDER BY contype"
            )
        ).all()
    }

    revision: str | None = None
    if "alembic_version" in tables:
        row = connection.execute(text("SELECT version_num FROM alembic_version")).first()
        revision = str(row[0]) if row else None

    app_role = bool(
        connection.execute(text("SELECT 1 FROM pg_roles WHERE rolname = 'bm_app'")).first()
    )

    row_counts: dict[str, int] = {}
    if count_rows:
        # Only tables that exist, and `count(*)` rather than a reltuples estimate: this
        # number is the evidence for "zero business records", and an estimate is not
        # evidence. At production scale these are all small or empty.
        for table in _business_tables():
            if table not in tables:
                continue
            total = int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())
            if total:
                row_counts[table] = total

    return Facts(
        server_version=scalar("SHOW server_version"),
        current_database=scalar("SELECT current_database()"),
        current_user=scalar("SELECT current_user"),
        extensions=extensions,
        tables=tables,
        enum_types=enum_types,
        index_count=index_count,
        constraint_counts=constraint_counts,
        alembic_revision=revision,
        app_role_exists=app_role,
        row_counts=row_counts,
    )


def _config_row_counts(connection: Connection, tables: list[str]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for table in sorted(CONFIG_TABLES - {"alembic_version"}):
        if table not in tables:
            continue
        counts[table] = int(
            connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
        )
    return counts


def _print_facts(target: Target, facts: Facts, config_counts: dict[str, int]) -> None:
    head = expected_head()
    print(f"target            {target.masked}")
    print(f"endpoint          {'POOLED (pgbouncer)' if _is_pooled(target) else 'DIRECT'}")
    print(f"server            PostgreSQL {facts.server_version}")
    print(f"connected as      {facts.current_user} on {facts.current_database}")
    print()
    print(f"alembic revision  {facts.alembic_revision or '(none — never migrated)'}")
    print(f"code expects      {head or '(indeterminate)'}")
    print(f"tables            {len(facts.tables)}")
    print(f"enum types        {len(facts.enum_types)} (code defines {len(ALL_ENUM_TYPES)})")
    print(f"indexes           {facts.index_count}")
    constraints = ", ".join(f"{k}={v}" for k, v in sorted(facts.constraint_counts.items()))
    print(f"constraints       {constraints or '(none)'}   [p=pk u=unique f=fk c=check]")
    print()
    missing_ext = [e for e in REQUIRED_EXTENSIONS if e not in facts.extensions]
    for name in REQUIRED_EXTENSIONS:
        version = facts.extensions.get(name)
        print(f"extension         {name:10} {version or 'MISSING'}")
    print()
    print(f"role bm_app       {'present' if facts.app_role_exists else 'ABSENT'}")
    if not facts.app_role_exists:
        print(
            "                  ^ every GRANT/REVOKE in the migrations is skipped while this\n"
            "                    role is absent, so append-only immutability would not be\n"
            "                    enforced. Run scripts/neon-init-roles.sql BEFORE migrating."
        )
    print()
    if config_counts:
        print("configuration rows")
        for table, total in config_counts.items():
            print(f"  {table:22} {total}")
        print()
    if facts.row_counts:
        print("BUSINESS DATA PRESENT")
        for table, total in sorted(facts.row_counts.items()):
            print(f"  {table:22} {total}")
    else:
        print("business data     none — every business table is empty")
    print()
    if missing_ext and facts.tables:
        print(f"note: extensions missing: {', '.join(missing_ext)}")


# --- create-app-role ---------------------------------------------------------


def _rewrite_env_database_url(env_file: Path, *, user: str, password: str) -> None:
    """Point `BM_DATABASE_URL` at `user:password`, keeping everything else byte-identical.

    Only the credential segment is replaced. The host, the `-pooler` suffix, the database
    name and the query string (`sslmode`, `channel_binding`) are carried through
    untouched, because those are the parts an operator verified against the Neon console
    and a rewrite that "helpfully" normalised one of them would be a silent change to the
    connection's security properties.
    """
    text_in = env_file.read_text(encoding="utf-8")
    pattern = re.compile(r"^(BM_DATABASE_URL=\w+(?:\+\w+)?://)[^@\s]*(@.*)$", re.MULTILINE)
    if not pattern.search(text_in):
        raise Refused(
            f"could not find a BM_DATABASE_URL line to rewrite in {env_file.name}.\n"
            "  Set it by hand to the pooled Neon URL for `bm_app`."
        )
    quoted = quote(password, safe="")
    text_out = pattern.sub(lambda m: f"{m.group(1)}{user}:{quoted}{m.group(2)}", text_in)
    env_file.write_text(text_out, encoding="utf-8", newline="\n")


def cmd_create_app_role(args: argparse.Namespace) -> int:
    """Run `neon-init-roles.sql`, then repoint `BM_DATABASE_URL` at the new role.

    Three things make this a subcommand rather than a documented copy-paste into the Neon
    SQL editor, and each of them is a mistake the manual route invites:

    1. **The password never reaches a terminal, a shell history, a log or a file other
       than the git-ignored `.env.production`.** The documented manual procedure is "edit
       two placeholders in a committed `.sql` file, run it, then paste the same value into
       your env file" — which puts a live production credential in an editor buffer, in
       the clipboard, and one `git add -A` away from a public repository.
    2. **It runs BEFORE the migrations, which is the whole point.** Every `GRANT`/`REVOKE`
       in migrations 0004, 0007, 0011 and 0013 is wrapped in `_app_role_exists()`, so a
       migration run without this role reports success and silently produces a schema with
       no append-only protection at all.
    3. **It verifies the role's attributes afterwards**, rather than trusting that the
       script that claimed to create it did so correctly.

    The SQL itself is still `scripts/neon-init-roles.sql`, read from disk and substituted
    in memory. One source of truth for the grants, and the file on disk keeps its
    placeholder.
    """
    target = _resolve("BM_MIGRATION_DATABASE_URL")
    _guard_target(target, args, variable="BM_MIGRATION_DATABASE_URL")

    if _is_pooled(target):
        raise Refused(
            "BM_MIGRATION_DATABASE_URL uses Neon's POOLED endpoint. `CREATE ROLE` and\n"
            "  `ALTER DEFAULT PRIVILEGES` need the DIRECT endpoint — remove `-pooler`."
        )

    sql_path = REPO_ROOT / "scripts" / "neon-init-roles.sql"
    script = sql_path.read_text(encoding="utf-8")
    if _ROLE_SQL_PLACEHOLDER not in script:
        raise Refused(
            f"{sql_path.name} no longer contains {_ROLE_SQL_PLACEHOLDER!r}.\n"
            "  Someone has already substituted a password into the committed file. Restore\n"
            "  the placeholder and ROTATE that password — it may be in git history."
        )

    # Supplied for a rotation, generated otherwise. 32 urlsafe bytes is ~43 characters.
    password = os.environ.get("BM_APP_DB_PASSWORD") or secrets.token_urlsafe(32)
    if len(password) < 24:
        raise Refused("BM_APP_DB_PASSWORD is shorter than 24 characters.")

    # The database name is interpolated into `GRANT CONNECT ON DATABASE`, so it has to
    # match the database actually being connected to or the grant lands on the wrong one.
    script = script.replace(_ROLE_SQL_PLACEHOLDER, password)
    if "DATABASE neondb " in script and target.database != "neondb":
        script = script.replace("DATABASE neondb ", f"DATABASE {target.database} ")

    env_file = REPO_ROOT / ".env.production"
    print()
    print(f"target            {target.masked}")
    print(f"sql               {sql_path.name}")
    print(
        f"password          {'from BM_APP_DB_PASSWORD' if os.environ.get('BM_APP_DB_PASSWORD') else 'generated'}"
        f" ({len(password)} chars, never printed)"
    )
    print(f"env to update     {env_file.name} -> BM_DATABASE_URL")
    print()

    if not args.yes:
        print("Nothing has been written. Re-run with --yes to apply.")
        return 0

    with _connect(target) as connection:
        # `exec_driver_sql`, NOT `execute(text(...))`.
        #
        # `text()` scans its string for `:name` bind parameters with a regex that knows
        # nothing about SQL comments — and `neon-init-roles.sql` quotes the Python guard
        # it is warning about, which contains `rolname = :role`. SQLAlchemy therefore
        # demanded a value for a bind parameter that exists only inside a comment, and
        # refused to compile the script at all. Handing the SQL straight to the driver
        # skips that parsing entirely, which is what is wanted for a file of literal DDL.
        #
        # Autocommit because this is a multi-statement script and `CREATE ROLE` is not
        # meaningfully rolled back halfway. Every statement in the file is idempotent, so
        # a partial run is re-runnable rather than broken.
        connection.execution_options(isolation_level="AUTOCOMMIT").exec_driver_sql(script)

    print("role SQL applied. Verifying attributes...")
    checks: list[tuple[bool, str]] = []
    with _connect(target) as connection:
        row = connection.execute(
            text(
                "SELECT rolcanlogin, rolsuper, rolcreatedb, rolcreaterole, rolbypassrls,"
                " rolreplication FROM pg_roles WHERE rolname = 'bm_app'"
            )
        ).first()
        if row is None:
            raise Refused("`bm_app` still does not exist after running the script.")
        can_login, is_super, can_createdb, can_createrole, bypass_rls, replication = row
        checks.append((bool(can_login), "bm_app can log in"))
        checks.append((not is_super, "bm_app is NOT a superuser"))
        checks.append((not can_createdb, "bm_app cannot CREATE DATABASE"))
        checks.append((not can_createrole, "bm_app cannot CREATE ROLE"))
        checks.append((not bypass_rls, "bm_app cannot bypass row-level security"))
        checks.append((not replication, "bm_app has no replication privilege"))
        checks.append(
            (
                bool(
                    connection.execute(
                        text("SELECT has_database_privilege('bm_app', :d, 'CONNECT')"),
                        {"d": target.database},
                    ).scalar_one()
                ),
                f"bm_app may CONNECT to {target.database}",
            )
        )
        checks.append(
            (
                bool(
                    connection.execute(
                        text("SELECT has_schema_privilege('bm_app', 'public', 'USAGE')")
                    ).scalar_one()
                ),
                "bm_app has USAGE on schema public",
            )
        )
        checks.append(
            (
                not connection.execute(
                    text("SELECT has_schema_privilege('bm_app', 'public', 'CREATE')")
                ).scalar_one(),
                "bm_app has NO CREATE on schema public (cannot add its own tables)",
            )
        )
        owner = connection.execute(
            text("SELECT pg_get_userbyid(nspowner) FROM pg_namespace WHERE nspname = 'public'")
        ).scalar_one()
        checks.append((str(owner) != "bm_app", f"schema public is owned by {owner}, not bm_app"))
        # `ALTER DEFAULT PRIVILEGES` is what makes the migrations' tables reachable at all.
        default_acl = connection.execute(
            text(
                "SELECT count(*) FROM pg_default_acl d"
                " JOIN pg_namespace n ON n.oid = d.defaclnamespace"
                " WHERE n.nspname = 'public'"
                "   AND array_to_string(d.defaclacl, ',') LIKE '%bm_app%'"
            )
        ).scalar_one()
        checks.append(
            (int(default_acl) >= 2, f"default privileges recorded for bm_app ({default_acl} rows)")
        )
        connection.rollback()

    failures = 0
    for ok, label in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1
    if failures:
        print(f"\n{failures} role check(s) FAILED. Not rewriting {env_file.name}.")
        return 2

    _rewrite_env_database_url(env_file, user="bm_app", password=password)
    print(f"\n{env_file.name}: BM_DATABASE_URL now uses bm_app (host and query string unchanged).")

    print()
    print("The password exists in exactly one place: .env.production, which is git-ignored.")
    print("Copy it from there into the backend host's secret store when you deploy.")
    print("Next: `production_db.py migrate --yes`.")
    return 0


# --- inspect -----------------------------------------------------------------


def cmd_inspect(args: argparse.Namespace) -> int:
    """Read-only. Opens no transaction that writes, runs no DDL, changes nothing."""
    target = _resolve(args.variable)
    _guard_target(target, args, variable=args.variable)

    print()
    with _connect(target) as connection:
        facts = _gather(connection, count_rows=True)
        config_counts = _config_row_counts(connection, facts.tables)
        _print_facts(target, facts, config_counts)
        connection.rollback()

    head = expected_head()
    print("verdict")
    if facts.is_untouched:
        print("  FRESH — no tables, no alembic_version. Safe to migrate.")
        print("  Next: scripts/neon-init-roles.sql, then `production_db.py migrate --yes`.")
        return 0
    if facts.alembic_revision is None:
        print("  UNSAFE — tables exist but there is no alembic_version row.")
        print("  Something created this schema outside Alembic. STOP and investigate.")
        return 2
    if facts.business_rows:
        print(f"  NOT EMPTY — {facts.business_rows} business rows across")
        print(f"              {len(facts.row_counts)} tables. This database is in use.")
        return 2
    if facts.alembic_revision == head:
        print(f"  MIGRATED AND EMPTY — at head {head}, zero business rows.")
        print("  Next: `production_db.py verify`, then `provision`.")
        return 0
    print(f"  BEHIND — at {facts.alembic_revision}, code expects {head}.")
    print("  Next: `production_db.py migrate --yes` applies the pending revisions.")
    return 0


# --- migrate -----------------------------------------------------------------


def cmd_migrate(args: argparse.Namespace) -> int:
    """`alembic upgrade head`, after proving the target is fresh enough to take it."""
    target = _resolve("BM_MIGRATION_DATABASE_URL")
    _guard_target(target, args, variable="BM_MIGRATION_DATABASE_URL")

    if _is_pooled(target):
        raise Refused(
            f"BM_MIGRATION_DATABASE_URL uses Neon's POOLED endpoint ({target.host}).\n"
            "  Alembic needs the DIRECT endpoint: migration 0001 runs CREATE EXTENSION and\n"
            "  CREATE TYPE, each revision runs in its own transaction, and PgBouncer in\n"
            "  transaction mode will not carry that reliably.\n"
            "  Remove `-pooler` from the hostname."
        )

    print()
    with _connect(target) as connection:
        facts = _gather(connection, count_rows=True)
        config_counts = _config_row_counts(connection, facts.tables)
        _print_facts(target, facts, config_counts)
        connection.rollback()

    head = expected_head()
    if head is None:
        raise Refused(
            "the code has no single Alembic head — `migrations/versions/` has a branch.\n"
            "  Merge it before migrating anything."
        )

    # --- pre-flight. Each of these STOPS rather than adapting. ----------------
    if facts.tables and facts.alembic_revision is None:
        raise Refused(
            f"{len(facts.tables)} tables exist but there is no `alembic_version` row.\n"
            "  This schema was not created by Alembic, so no revision can be trusted to\n"
            "  describe it. Nothing has been changed.\n\n"
            "  Do NOT drop the objects to force a clean migration. Work out where they came\n"
            "  from first — most likely a different project shares this Neon branch, or a\n"
            "  schema was restored by hand. Either way the fix is a new, empty branch."
        )

    if facts.business_rows:
        listing = ", ".join(f"{t}={n}" for t, n in sorted(facts.row_counts.items()))
        raise Refused(
            f"this database holds {facts.business_rows} business rows ({listing}).\n"
            "  It is in use, so this is not a first-time production migration and the\n"
            "  freshness assumption behind this command does not hold.\n\n"
            "  Nothing has been changed. For a routine schema upgrade of a live database,\n"
            "  follow `docs/PRODUCTION-DEPLOYMENT-GUIDE.md §17`: confirm a fresh Neon\n"
            "  snapshot, then run `alembic upgrade head` as a deliberate, announced step."
        )

    if facts.alembic_revision == head:
        print(f"nothing to do — already at head {head}.")
        return 0

    if not facts.app_role_exists and not args.allow_missing_app_role:
        raise Refused(
            "the `bm_app` role does not exist on this database.\n\n"
            "  Migrating now would produce a COMPLETE schema with NO append-only\n"
            "  protection. Every GRANT/REVOKE in migrations 0004, 0007, 0011 and 0013 is\n"
            "  wrapped in `_app_role_exists()` and is skipped silently when the role is\n"
            "  absent — the migration reports success and `UPDATE audit_logs`,\n"
            "  `DELETE FROM stock_movements` and `UPDATE invoices` all become possible.\n"
            "  Nothing reports it afterwards. That is NFR-DATA-006 gone.\n\n"
            "  Run `scripts/neon-init-roles.sql` first, then come back.\n"
            "  (--allow-missing-app-role exists for a staging branch where you accept this.)"
        )

    if not args.yes:
        print("pre-flight passed. Nothing has been written.")
        print()
        print(f"  would upgrade  {facts.alembic_revision or 'base'} -> {head}")
        print(f"  on             {target.masked}")
        print()
        print("Re-run with --yes to apply.")
        return 0

    # The URL Alembic uses is the one that was just verified, set explicitly rather than
    # inherited. `migrations/env.py` reads this variable and `_load_dotenv()` only fills
    # gaps, so this cannot be overridden by a stray `.env`.
    os.environ["BM_MIGRATION_DATABASE_URL"] = target.url

    from alembic import command
    from alembic.config import Config

    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))

    print(f"applying {facts.alembic_revision or 'base'} -> {head} on {target.masked}")
    print()
    command.upgrade(config, "head")
    print()
    print("upgrade complete. Run `production_db.py verify` next.")
    return 0


# --- verify ------------------------------------------------------------------


def cmd_verify(args: argparse.Namespace) -> int:
    """Assert the migrated schema is what the code expects, and that it holds no data."""
    target = _resolve(args.variable)
    _guard_target(target, args, variable=args.variable)

    print()
    checks: list[tuple[bool, str]] = []

    with _connect(target) as connection:
        facts = _gather(connection, count_rows=True)
        config_counts = _config_row_counts(connection, facts.tables)
        _print_facts(target, facts, config_counts)

        head = expected_head()
        checks.append(
            (
                facts.alembic_revision == head,
                f"alembic_version is at the code's head ({head})",
            )
        )

        expected_tables = set(Base.metadata.tables) | {"alembic_version"}
        actual = set(facts.tables)
        checks.append(
            (
                expected_tables <= actual,
                f"every ORM table exists ({len(expected_tables)} expected)"
                + (
                    f" — missing {sorted(expected_tables - actual)}"
                    if expected_tables - actual
                    else ""
                ),
            )
        )
        extra = actual - expected_tables
        checks.append(
            (not extra, "no unexpected tables" + (f" — found {sorted(extra)}" if extra else ""))
        )

        for name in REQUIRED_EXTENSIONS:
            checks.append((name in facts.extensions, f"extension {name} installed"))

        checks.append(
            (
                len(facts.enum_types) == len(ALL_ENUM_TYPES),
                f"all {len(ALL_ENUM_TYPES)} enum types created (found {len(facts.enum_types)})",
            )
        )

        checks.append((facts.index_count > 50, f"indexes present ({facts.index_count})"))
        checks.append(
            (
                facts.constraint_counts.get("f", 0) > 0 and facts.constraint_counts.get("c", 0) > 0,
                "foreign keys and check constraints present",
            )
        )

        # Migration 0021 exists because this one constraint was created only
        # `if _app_role_exists()`, so every CI run and every single-role cluster produced
        # a schema silently missing it. Asserted rather than trusted.
        fk_present = bool(
            connection.execute(
                text("SELECT 1 FROM pg_constraint WHERE conname = 'fk_counters__invoice_series'")
            ).first()
        )
        checks.append((fk_present, "fk_counters__invoice_series exists (migration 0021)"))

        # The search indexes NFR-PERF-001 depends on. Migration 0006 described them and
        # never created them; 0018 does. Without them every product search is a
        # sequential scan, which a seeded test catalogue cannot detect.
        for index in (
            "ix_products__sku_prefix",
            "ix_products__name_prefix",
            "ix_products__search_trgm",
        ):
            present = bool(
                connection.execute(
                    text("SELECT 1 FROM pg_indexes WHERE indexname = :n"), {"n": index}
                ).first()
            )
            checks.append((present, f"search index {index} exists (migration 0018)"))

        # --- the privilege model, which is the only proof immutability is real --
        checks.append((facts.app_role_exists, "role bm_app exists"))
        if facts.app_role_exists:
            for table, privilege in (
                ("audit_logs", "UPDATE"),
                ("audit_logs", "DELETE"),
                ("stock_movements", "UPDATE"),
                ("stock_movements", "DELETE"),
                ("payments", "UPDATE"),
                ("payments", "DELETE"),
                ("invoices", "DELETE"),
            ):
                granted = bool(
                    connection.execute(
                        text("SELECT has_table_privilege('bm_app', :t, :p)"),
                        {"t": table, "p": privilege},
                    ).scalar_one()
                )
                checks.append((not granted, f"bm_app CANNOT {privilege} {table}"))

            for table, privilege in (
                ("invoices", "INSERT"),
                ("invoice_items", "INSERT"),
                ("stock_movements", "INSERT"),
                ("payments", "INSERT"),
                ("audit_logs", "INSERT"),
                ("products", "UPDATE"),
            ):
                granted = bool(
                    connection.execute(
                        text("SELECT has_table_privilege('bm_app', :t, :p)"),
                        {"t": table, "p": privilege},
                    ).scalar_one()
                )
                checks.append((granted, f"bm_app CAN {privilege} {table}"))

            # The enumerated mutable columns, asserted as an EXACT SET rather than by spot
            # check.
            #
            # `SECURITY.md §7` names the columns that may change on a finalised invoice —
            # the cancellation fields, the derived payment cache, and `updated_at` — and
            # the point of a column-level grant is that everything else is impossible,
            # including by a future bug or a careless migration using the app role. A spot
            # check on three columns would pass just as happily if a migration added
            # `GRANT UPDATE (total)`, which is the exact failure the model exists to
            # prevent. Comparing the whole set is the only form of this check that cannot
            # be satisfied by an over-broad grant.
            #
            # Read from `information_schema.column_privileges`, which lists only the
            # columns actually granted, so a new column defaults to "not permitted" and a
            # deliberate addition has to be made here too.
            for table, expected in (
                (
                    "invoices",
                    {
                        "status",
                        "cancelled_at",
                        "cancelled_by_user_id",
                        "cancellation_reason",
                        "amount_paid",
                        "payment_status",
                        "updated_at",
                    },
                ),
                ("invoice_items", {"returned_quantity"}),
                ("payments", set()),
                ("stock_movements", set()),
                ("audit_logs", set()),
            ):
                actual = {
                    str(r[0])
                    for r in connection.execute(
                        text(
                            "SELECT column_name FROM information_schema.column_privileges"
                            " WHERE table_schema = 'public' AND table_name = :t"
                            "   AND grantee = 'bm_app' AND privilege_type = 'UPDATE'"
                        ),
                        {"t": table},
                    ).all()
                }
                extra = sorted(actual - expected)
                missing = sorted(expected - actual)
                checks.append(
                    (
                        actual == expected,
                        f"bm_app's UPDATE grant on {table} is exactly the "
                        f"{len(expected)} documented column(s)"
                        + (f" — EXTRA {extra}" if extra else "")
                        + (f" — MISSING {missing}" if missing else ""),
                    )
                )

        # --- sequences, functions, triggers, materialized views -----------------
        #
        # None of these is a formality. Each is a way the schema can be structurally
        # complete and behaviourally wrong, and none of them shows up in a table count.
        sequences = {
            str(r[0])
            for r in connection.execute(
                text(
                    "SELECT sequence_name FROM information_schema.sequences"
                    " WHERE sequence_schema = 'public'"
                )
            ).all()
        }
        # Exactly one sequence is correct here, and the distinction matters.
        #
        # This schema uses UUID primary keys throughout, and the sale reference is
        # allocated by a `FOR UPDATE` on `invoice_series` **inside** the finalisation
        # transaction rather than from a sequence — deliberately, because a sequence
        # increments outside the transaction and a rolled-back sale would consume a
        # number (ADR-009). So a sequence anywhere near money or numbering would be a
        # defect.
        #
        # `import_jobs_ordinal_seq` is not that. Migration 0020 added it because
        # `GET /imports` promised newest-first and ordered by `created_at DESC, id DESC`
        # — and `created_at` is not unique (it is `now()`, the transaction timestamp) while
        # `id` is `uuid4`, so the tiebreak was random and the keyset cursor could skip or
        # repeat a row. A `bigserial` gives pagination a monotonic, unique column. It
        # carries no financial meaning and a gap in it is harmless.
        #
        # Asserted as an exact set rather than a count, so a future `SERIAL` on an invoice
        # or payment table fails this check by name instead of just moving a number.
        expected_sequences = {"import_jobs_ordinal_seq"}
        checks.append(
            (
                sequences == expected_sequences,
                f"exactly the one expected sequence ({sorted(expected_sequences)});"
                " money and numbering use no sequence (ADR-009)"
                + (
                    f" — EXTRA {sorted(sequences - expected_sequences)}"
                    if sequences - expected_sequences
                    else ""
                )
                + (
                    f" — MISSING {sorted(expected_sequences - sequences)}"
                    if expected_sequences - sequences
                    else ""
                ),
            )
        )
        # `bm_app` INSERTs import jobs, so it needs the sequence it defaults from.
        # `ALTER DEFAULT PRIVILEGES ... ON SEQUENCES` in `neon-init-roles.sql` is what
        # grants this, and it only applies to sequences created afterwards — so this is
        # also a second, independent check that the role was created before the migration.
        for sequence in sorted(sequences):
            granted = bool(
                connection.execute(
                    text("SELECT has_sequence_privilege('bm_app', :s, 'USAGE, SELECT')"),
                    {"s": sequence},
                ).scalar_one()
            )
            checks.append((granted, f"bm_app has USAGE on sequence {sequence}"))

        # `gen_random_uuid()` is called in runtime SQL (`domain/sales/numbering.py`,
        # `domain/inventory/service.py`, `api/v1/inventory.py`). Core since PostgreSQL 13,
        # but asserted rather than assumed: on an older server it would need pgcrypto, and
        # the failure would surface as a broken first sale rather than a missing function.
        checks.append(
            (
                bool(
                    connection.execute(
                        text("SELECT to_regprocedure('gen_random_uuid()') IS NOT NULL")
                    ).scalar_one()
                ),
                "gen_random_uuid() is callable (used in runtime SQL)",
            )
        )
        for fn in ("similarity(text,text)", "unaccent(text)"):
            checks.append(
                (
                    bool(
                        connection.execute(
                            text("SELECT to_regprocedure(:f) IS NOT NULL"), {"f": fn}
                        ).scalar_one()
                    ),
                    f"extension function {fn} is callable",
                )
            )

        triggers = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM pg_trigger t JOIN pg_class c ON c.oid = t.tgrelid"
                    " JOIN pg_namespace n ON n.oid = c.relnamespace"
                    " WHERE n.nspname = 'public' AND NOT t.tgisinternal"
                )
            ).scalar_one()
        )
        # Immutability is enforced by PRIVILEGE, not by a trigger (`SECURITY.md §7`), and
        # no migration creates one. A trigger appearing here would be an undocumented
        # second enforcement path — reported rather than silently tolerated.
        checks.append((triggers == 0, f"no user triggers ({triggers}) — enforcement is by grant"))

        matviews = [
            str(r[0])
            for r in connection.execute(
                text("SELECT matviewname FROM pg_matviews WHERE schemaname = 'public'")
            ).all()
        ]
        # No derived financial state is cached in a materialized view: `amount_paid` and
        # `payment_status` are columns written in the same transaction as the allocations
        # (ADR-022). A matview would be a second, staler source of truth for money.
        checks.append(
            (
                not matviews,
                f"no materialized views ({matviews or 'none'}) — derived money is"
                " transactional, not cached",
            )
        )

        # --- reference data from the migrations ---------------------------------
        checks.append(
            (config_counts.get("permissions", 0) > 20, "permissions seeded (migration 0005)")
        )
        checks.append((config_counts.get("roles", 0) == 6, "6 system roles seeded"))
        checks.append((config_counts.get("role_permissions", 0) > 0, "role_permissions seeded"))

        # Every permission the code knows about must exist as a row, or a role mapping
        # silently grants nothing. The enum is the source of truth.
        from bm.domain.auth.permissions import Permission

        db_permissions = {
            str(r[0]) for r in connection.execute(text("SELECT code FROM permissions")).all()
        }
        code_permissions = {str(p) for p in Permission}
        checks.append(
            (
                code_permissions == db_permissions,
                f"permissions table matches the Permission enum exactly"
                f" ({len(code_permissions)} in code, {len(db_permissions)} in db)"
                + (
                    f" — missing {sorted(code_permissions - db_permissions)}"
                    if code_permissions - db_permissions
                    else ""
                )
                + (
                    f" — extra {sorted(db_permissions - code_permissions)}"
                    if db_permissions - code_permissions
                    else ""
                ),
            )
        )
        orphan_role_perms = int(
            connection.execute(
                text(
                    "SELECT count(*) FROM role_permissions rp"
                    " LEFT JOIN permissions p ON p.code = rp.permission_code"
                    " WHERE p.code IS NULL"
                )
            ).scalar_one()
        )
        checks.append((orphan_role_perms == 0, "no role_permissions row points at a missing code"))

        # --- zero business data --------------------------------------------------
        checks.append(
            (
                facts.business_rows == 0,
                f"zero rows in all {len(_business_tables())} business tables"
                + (f" — found {sorted(facts.row_counts)}" if facts.row_counts else ""),
            )
        )
        checks.append(
            (
                config_counts.get("invoice_series", 0) == 0
                if "invoice_series" in config_counts
                else True,
                "no invoice series yet (created lazily by the first sale)",
            )
        )

        # --- cross-table consistency of the configuration rows ------------------
        #
        # Zero business rows is not the same as a coherent configuration. A business with
        # no owner, an owner with no membership, or a counter pointing at a branch of a
        # different business would all pass a row-count check and leave a database nobody
        # can sign in to — which is exactly the state a half-finished provision produces.
        if config_counts.get("businesses", 0):
            for label, sql in (
                (
                    "every branch belongs to an existing business",
                    "SELECT count(*) FROM branches b"
                    " LEFT JOIN businesses x ON x.id = b.business_id WHERE x.id IS NULL",
                ),
                (
                    "every counter's branch belongs to the counter's own business",
                    "SELECT count(*) FROM counters c JOIN branches b ON b.id = c.branch_id"
                    " WHERE b.business_id <> c.business_id",
                ),
                (
                    "every user belongs to an existing business",
                    "SELECT count(*) FROM users u"
                    " LEFT JOIN businesses x ON x.id = u.business_id WHERE x.id IS NULL",
                ),
                (
                    "every user has at least one membership",
                    "SELECT count(*) FROM users u"
                    " WHERE NOT EXISTS (SELECT 1 FROM memberships m WHERE m.user_id = u.id)",
                ),
                (
                    "every membership points at an existing role",
                    "SELECT count(*) FROM memberships m"
                    " LEFT JOIN roles r ON r.id = m.role_id WHERE r.id IS NULL",
                ),
                (
                    "at least one user holds the owner role",
                    "SELECT CASE WHEN EXISTS (SELECT 1 FROM memberships m"
                    " JOIN roles r ON r.id = m.role_id WHERE r.code = 'owner')"
                    " THEN 0 ELSE 1 END",
                ),
                (
                    "every unit belongs to an existing business",
                    "SELECT count(*) FROM units u"
                    " LEFT JOIN businesses x ON x.id = u.business_id WHERE x.id IS NULL",
                ),
                (
                    "no business identity looks like demo or fixture data",
                    "SELECT count(*) FROM businesses WHERE display_name ILIKE '%DEMO%'"
                    " OR display_name ILIKE '%E2E%' OR display_name ILIKE '%NOT A REAL%'"
                    " OR display_name ILIKE '%TEST%'",
                ),
                (
                    "no seeded username survived (demo-owner / e2e-owner)",
                    "SELECT count(*) FROM users"
                    " WHERE username IN ('demo-owner', 'e2e-owner', 'test', 'admin')",
                ),
                (
                    "every active user has a password hash",
                    "SELECT count(*) FROM users"
                    " WHERE is_active AND (password_hash IS NULL OR length(password_hash) < 20)",
                ),
                (
                    "every password hash is Argon2id",
                    "SELECT count(*) FROM users WHERE password_hash NOT LIKE '$argon2id$%'",
                ),
            ):
                bad = int(connection.execute(text(sql)).scalar_one())
                checks.append((bad == 0, f"{label}" + (f" — {bad} violation(s)" if bad else "")))

            units_seeded = config_counts.get("units", 0)
            from bm.domain.catalog.units import DEFAULT_UNITS as _UNITS

            expected_units = len(_UNITS) * config_counts.get("businesses", 0)
            checks.append(
                (
                    units_seeded == expected_units,
                    f"default units present ({units_seeded}/{expected_units}) — `products`"
                    " has a RESTRICT FK to `units`, so without them no product can be created",
                )
            )

        connection.rollback()

    # --- the runtime role, connecting for real -------------------------------
    #
    # Everything above was asked of `neondb_owner` via `has_table_privilege`, which
    # answers "what does the catalogue say bm_app may do". That is not the same question
    # as "can bm_app actually connect and work", and the two differ for reasons the
    # catalogue cannot show: a wrong password in `BM_DATABASE_URL`, a pooled endpoint that
    # has not picked the role up yet, `channel_binding` the client cannot satisfy, or a
    # Neon IP allow-list. Each of those produces a perfect privilege report and an API
    # that cannot reach its database.
    #
    # Strictly read-only. It runs SELECTs and one deliberately-failing write inside a
    # transaction that is rolled back, so no row and no sequence value is consumed — the
    # zero-state guarantee is not spent proving the privileges work.
    if not args.skip_runtime_check:
        runtime = _resolve("BM_DATABASE_URL")
        _guard_target(runtime, args, variable="BM_DATABASE_URL")
        print()
        print(f"runtime role      {runtime.masked}")
        print(f"endpoint          {'POOLED (pgbouncer)' if _is_pooled(runtime) else 'DIRECT'}")
        checks.append(
            (
                runtime.user != "neondb_owner",
                f"runtime URL does NOT use the admin role (it uses {runtime.user!r})",
            )
        )
        checks.append((_is_pooled(runtime), "runtime URL uses Neon's pooled endpoint"))
        checks.append(("sslmode=require" in runtime.url, "runtime URL requires SSL"))

        with _connect(runtime) as rc:
            who = str(rc.execute(text("SELECT current_user")).scalar_one())
            checks.append((who == "bm_app", f"connected as {who} (expected bm_app)"))

            pass

            # Can it do its job? One SELECT per table the billing path reads first.
            for table in ("permissions", "roles", "units", "businesses", "branches", "counters"):
                try:
                    rc.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one()
                    ok, note = True, ""
                except DBAPIError as exc:
                    ok, note = False, f" — {type(exc.orig).__name__}"
                checks.append((ok, f"bm_app can SELECT {table}{note}"))

            # And can it NOT do what the migrations revoked? Attempted for real, because
            # a `has_table_privilege` answer and the server's actual refusal have come
            # apart before — a column grant can leave a table grant looking sufficient.
            # Rolled back either way, so nothing is written even if the REVOKE is missing.
            for label, sql in (
                ("UPDATE audit_logs", "UPDATE audit_logs SET action = action"),
                ("DELETE stock_movements", "DELETE FROM stock_movements"),
                # `total`, not `total_amount` — the latter does not exist, and naming it
                # made this probe fail with UndefinedColumn, which looks identical to a
                # refusal if you only check that an exception was raised. The check now
                # names a real column, so a PASS means the privilege was enforced rather
                # than that the statement was malformed.
                ("UPDATE invoices.total", "UPDATE invoices SET total = total"),
                ("DELETE invoices", "DELETE FROM invoices"),
                ("DELETE payments", "DELETE FROM payments"),
                (
                    "UPDATE invoice_items.line_total",
                    "UPDATE invoice_items SET line_total = line_total",
                ),
            ):
                try:
                    rc.execute(text(sql))
                    refused = False
                except DBAPIError:
                    refused = True
                rc.rollback()
                checks.append((refused, f"bm_app is REFUSED {label} by the server"))

            # DDL must be impossible: no CREATE on schema public.
            try:
                rc.execute(text("CREATE TABLE bm_app_should_not_be_able_to_do_this (x int)"))
                ddl_refused = False
            except DBAPIError:
                ddl_refused = True
            rc.rollback()
            checks.append((ddl_refused, "bm_app is REFUSED CREATE TABLE by the server"))
            rc.rollback()

        # --- is the transport actually encrypted? --------------------------------
        #
        # Proved by making the server refuse plaintext, because on Neon nothing else can
        # prove it. Two obvious approaches both fail here, and silently:
        #
        #   * `pg_stat_ssl WHERE pid = pg_backend_pid()` reports `ssl = false` on BOTH the
        #     pooled and the direct endpoint. Every Neon endpoint sits behind Neon's own
        #     proxy, which terminates TLS and speaks to the Postgres compute over its
        #     internal network — so the backend's view is never the client's leg. Trusting
        #     it would report an encryption failure on a correctly encrypted connection.
        #   * `psycopg.ConnectionInfo.ssl_in_use` does not exist. That is a psycopg2 API;
        #     psycopg 3.3 exposes no SSL attribute at all.
        #
        # So the check is an experiment rather than an observation: ask for `sslmode=disable`
        # and require the attempt to FAIL. Neon answers
        # `ERROR: connection is insecure (try using sslmode=require)`, which is the server
        # refusing plaintext — a stronger statement than any client-side flag, because it
        # holds for every client that will ever connect, including the deployed backend.
        insecure_url = re.sub(r"sslmode=\w+", "sslmode=disable", runtime.url)
        if insecure_url == runtime.url:
            checks.append((False, "runtime URL has no sslmode parameter to test"))
        else:
            try:
                probe = create_engine(insecure_url, connect_args={"connect_timeout": 15})
                with probe.connect() as pc:
                    pc.execute(text("SELECT 1"))
                plaintext_accepted = True
                probe.dispose()
            except SQLAlchemyError:
                plaintext_accepted = False
            checks.append(
                (
                    not plaintext_accepted,
                    "the server REFUSES an unencrypted connection (sslmode=disable is"
                    " rejected), so the transport cannot silently fall back to plaintext",
                )
            )
            checks.append(
                (
                    "channel_binding=require" in runtime.url,
                    "runtime URL requires SCRAM channel binding, which only exists over"
                    " TLS and binds authentication to the TLS channel",
                )
            )

    print()
    print("checks")
    failures = 0
    for ok, label in checks:
        print(f"  {'PASS' if ok else 'FAIL'}  {label}")
        if not ok:
            failures += 1

    print()
    if failures:
        print(f"{failures} of {len(checks)} checks FAILED. Do not deploy against this database.")
        return 2
    print(f"all {len(checks)} checks passed.")
    if not config_counts.get("businesses"):
        print("Next: `production_db.py provision` to create the business, branch, counter")
        print("and owner account. Without them nobody can sign in.")
    return 0


# --- zero-state --------------------------------------------------------------

#: Why each configuration table may hold rows, and how many to expect. `{n}` is the
#: number of businesses, so the expectation scales rather than being hard-coded to one.
_CONFIG_EXPECTATIONS: dict[str, tuple[str, str]] = {
    "alembic_version": ("1", "migration metadata — the applied revision"),
    "permissions": ("24", "reference data, migration 0005 (matches the Permission enum)"),
    "roles": ("6", "reference data, migration 0005 (the system roles)"),
    "role_permissions": (">0", "reference data, migration 0005 (role -> permission mapping)"),
    "units": ("11 x businesses", "reference data — `products` has a RESTRICT FK to it"),
    "businesses": ("1", "the shop itself — configuration, not business activity"),
    "branches": (">=1", "required: every counter and invoice is branch-scoped"),
    "counters": (">=1", "required: a sale cannot be finalised without a counter"),
    "users": (">=1", "the owner account — nobody can sign in without it"),
    "memberships": (">=1", "binds the owner to the `owner` role; no role = no permissions"),
    "settings": ("0", "defaults are applied IN CODE so an unset key is never ambiguous"),
}


def cmd_zero_state(args: argparse.Namespace) -> int:
    """Count every table individually and report expected vs actual.

    `verify` already asserts the aggregate ("zero rows across all business tables"), and
    that is the check that gates a deploy. This exists because an aggregate is not an
    audit: it cannot be read later to confirm *which* tables were examined, and a table
    added by a future migration would be covered silently rather than visibly.

    The business list is `Base.metadata` minus the configuration tables, so it is
    exhaustive by construction — every operational, child, financial and history table in
    the schema is counted, including ones nobody thought to name.

    Read-only. `--markdown` emits the table for the readiness report.
    """
    target = _resolve(args.variable)
    _guard_target(target, args, variable=args.variable)

    business = _business_tables()
    rows: list[tuple[str, str, str, int, bool, str]] = []

    with _connect(target) as connection:
        present = {
            str(r[0])
            for r in connection.execute(
                text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
            ).all()
        }
        businesses = int(
            connection.execute(text("SELECT count(*) FROM businesses")).scalar_one()
            if "businesses" in present
            else 0
        )

        for table in sorted(set(business) | set(_CONFIG_EXPECTATIONS)):
            if table not in present:
                rows.append((table, "config", "-", -1, False, "TABLE MISSING"))
                continue
            actual = int(connection.execute(text(f'SELECT count(*) FROM "{table}"')).scalar_one())

            if table in _CONFIG_EXPECTATIONS:
                expected, reason = _CONFIG_EXPECTATIONS[table]
                if expected == "11 x businesses":
                    ok = actual == 11 * businesses
                    expected = str(11 * businesses)
                elif expected.startswith(">="):
                    ok = actual >= int(expected[2:])
                elif expected.startswith(">"):
                    ok = actual > int(expected[1:])
                else:
                    ok = actual == int(expected)
                rows.append((table, "config", expected, actual, ok, reason))
            else:
                ok = actual == 0
                rows.append(
                    (
                        table,
                        "business",
                        "0",
                        actual,
                        ok,
                        "" if ok else "NON-ZERO — business activity present before go-live",
                    )
                )
        connection.rollback()

    biz = [r for r in rows if r[1] == "business"]
    cfg = [r for r in rows if r[1] == "config"]
    failures = [r for r in rows if not r[4]]

    if args.markdown:
        print("| Table | Kind | Expected | Actual | Result | Note |")
        print("|---|---|--:|--:|---|---|")
        for table, kind, expected, actual, ok, reason in rows:
            shown = "n/a" if actual < 0 else str(actual)
            print(
                f"| `{table}` | {kind} | {expected} | {shown} | "
                f"{'PASS' if ok else '**FAIL**'} | {reason} |"
            )
    else:
        print()
        print(f"{'table':28} {'kind':9} {'expected':>12} {'actual':>8}  result")
        print("-" * 74)
        for table, kind, expected, actual, ok, reason in rows:
            shown = "n/a" if actual < 0 else str(actual)
            note = f"  {reason}" if not ok or kind == "config" else ""
            print(
                f"{table:28} {kind:9} {expected:>12} {shown:>8}  {'PASS' if ok else 'FAIL'}{note}"
            )

    print()
    print(f"business tables : {len(biz)} counted, {sum(r[3] for r in biz if r[3] > 0)} total rows")
    print(f"config tables   : {len(cfg)} counted")
    print(f"result          : {'ALL PASS' if not failures else f'{len(failures)} FAILED'}")
    return 0 if not failures else 2


# --- provision ---------------------------------------------------------------


def cmd_provision(args: argparse.Namespace) -> int:
    """Create the five configuration rows that make the application reachable.

    Not demo data, and not business data: a business record, a branch, a counter, one
    owner user and the membership that gives it the `owner` role. Without them the schema
    is complete and nobody can sign in — there is no bootstrap endpoint, because creating
    the first owner is a deployment step (`CLAUDE.md §10`).

    What it still does not write: a product, a customer, a supplier, stock, an invoice, a
    draft, a sale or a payment. There is no statement in this function that touches any of
    those tables.
    """
    target = _resolve(args.variable)
    _guard_target(target, args, variable=args.variable)

    password = os.environ.get("BM_PROVISION_OWNER_PASSWORD", "")
    if not password:
        raise Refused(
            "BM_PROVISION_OWNER_PASSWORD is not set.\n\n"
            "  The owner's first password is read from the environment rather than a flag,\n"
            "  so it does not land in shell history or in a process listing:\n\n"
            '    $env:BM_PROVISION_OWNER_PASSWORD = "<a long passphrase>"\n'
            "    ./scripts/prod.ps1 python scripts/production_db.py provision --yes\n\n"
            "  It is stored only as an Argon2id hash. With --force-password-change (the\n"
            "  default) the owner must replace it before any other endpoint will answer."
        )
    if len(password) < 12:
        raise Refused("BM_PROVISION_OWNER_PASSWORD is shorter than 12 characters.")

    pepper = os.environ.get("BM_PASSWORD_PEPPER", "")
    if len(pepper) < 32:
        raise Refused(
            "BM_PASSWORD_PEPPER is missing or shorter than 32 characters.\n"
            "  It must be the SAME value Railway runs with. A hash peppered with a\n"
            "  different value authenticates nobody, and the failure is indistinguishable\n"
            "  from a wrong password."
        )
    for marker in _PLACEHOLDER_MARKERS:
        if marker in pepper:
            raise Refused(f"BM_PASSWORD_PEPPER still contains the placeholder {marker!r}.")

    from bm.domain.auth.hashing import hash_password

    business_id = uuid.uuid4()
    branch_id = uuid.uuid4()
    counter_id = uuid.uuid4()
    user_id = uuid.uuid4()
    owner_role_id = uuid.uuid5(_ROLE_NAMESPACE, "system-role:owner")

    print()
    print(f"target            {target.masked}")
    print(f"mode              {'DRY RUN — nothing will be written' if args.dry_run else 'writing'}")
    print()

    with _connect(target) as connection:
        existing = connection.execute(
            text("SELECT id, display_name FROM businesses ORDER BY created_at")
        ).all()
        if existing:
            connection.rollback()
            listing = "\n".join(f"    {row[0]}  {row[1]!r}" for row in existing)
            raise Refused(
                f"a business record already exists on this database:\n{listing}\n\n"
                "  Provisioning is a once-per-database step and will not run again — a second\n"
                "  business would be a second tenant, and every query is scoped by\n"
                "  `business_id` from the token, so the shop would silently see an empty\n"
                "  catalogue.\n\n"
                "  To correct an identity typed wrong, use `scripts/set_business_identity.py`.\n"
                "  To add a branch, counter or user, use the admin screens."
            )

        role = connection.execute(
            text("SELECT 1 FROM roles WHERE id = :id"), {"id": owner_role_id}
        ).first()
        if not role:
            connection.rollback()
            raise Refused(
                f"the system role `owner` ({owner_role_id}) is not present.\n"
                "  Migration 0005 seeds it. Run `production_db.py migrate --yes` first."
            )

        print("would create" if args.dry_run else "creating")
        print(f"  business   {business_id}  {args.display_name!r}")
        print(f"             address  {args.address_line!r}")
        print(f"             phones   {args.phone!r}")
        print(f"             timezone {args.timezone}, FY starts month {args.fy_start_month}")
        print("             legal_name NULL, tax_identifier NULL  (OD-4 open; never guessed)")
        print(f"  branch     {branch_id}  {args.branch_code}/{args.branch_name!r}")
        print(
            f"  counter    {counter_id}  {args.counter_code}/{args.counter_name!r}"
            f"  max_open_drafts={args.max_open_drafts}"
        )
        print(f"  user       {user_id}  {args.owner_username!r} / {args.owner_full_name!r}")
        print(f"             must_change_password={args.force_password_change}")
        print("  membership owner role, all branches")
        from bm.domain.catalog.units import DEFAULT_UNITS as _UNITS

        print(
            f"  units      {len(_UNITS)} reference rows ({', '.join(c for c, _, _, _ in _UNITS)})"
        )
        print()

        if args.dry_run:
            connection.rollback()
            print("dry run — nothing written.")
            return 0

        if not args.yes:
            connection.rollback()
            print("Re-run with --yes to apply.")
            return 0

        # One transaction: a half-provisioned tenant (a business with no owner, or an
        # owner with no membership) is unusable and harder to diagnose than none at all.
        connection.execute(
            text(
                "INSERT INTO businesses (id, display_name, address_lines, phone_numbers,"
                " timezone, financial_year_start_month)"
                " VALUES (:id, :name, :address, :phones, :tz, :fy)"
            ),
            {
                "id": business_id,
                "name": args.display_name,
                "address": args.address_line,
                "phones": args.phone,
                "tz": args.timezone,
                "fy": args.fy_start_month,
            },
        )
        connection.execute(
            text(
                "INSERT INTO branches (id, business_id, code, name, is_active)"
                " VALUES (:id, :b, :code, :name, true)"
            ),
            {
                "id": branch_id,
                "b": business_id,
                "code": args.branch_code,
                "name": args.branch_name,
            },
        )
        connection.execute(
            text(
                "INSERT INTO counters (id, business_id, branch_id, code, name, is_active,"
                " max_open_drafts) VALUES (:id, :b, :br, :code, :name, true, :drafts)"
            ),
            {
                "id": counter_id,
                "b": business_id,
                "br": branch_id,
                "code": args.counter_code,
                "name": args.counter_name,
                "drafts": args.max_open_drafts,
            },
        )
        connection.execute(
            text(
                "INSERT INTO users (id, business_id, username, full_name, password_hash,"
                " is_active, must_change_password)"
                " VALUES (:id, :b, :u, :full_name, :h, true, :must_change)"
            ),
            {
                "id": user_id,
                "b": business_id,
                "u": args.owner_username,
                "full_name": args.owner_full_name,
                "h": hash_password(password, pepper=pepper),
                "must_change": args.force_password_change,
            },
        )
        connection.execute(
            text(
                "INSERT INTO memberships (id, user_id, business_id, branch_id, role_id)"
                # `branch_id` NULL means every branch, which is what an owner holds.
                " VALUES (:id, :u, :b, NULL, :r)"
            ),
            {"id": uuid.uuid4(), "u": user_id, "b": business_id, "r": owner_role_id},
        )

        # Default units, for the business just created.
        #
        # Migration 0016 seeds units `for business_id in businesses` — and on a fresh
        # production database that loop runs zero times, because the business does not
        # exist yet. The migration's own docstring says so and points at
        # `CatalogService.ensure_default_units` as the path for a business created
        # afterwards, which is reachable at `POST /api/v1/catalog/units:ensure-defaults`.
        #
        # Seeding them here instead means the database is usable the moment it is
        # provisioned: `units` has a RESTRICT foreign key from `products`, so without a
        # unit the shop cannot create its first product, and the remedy would be an
        # undocumented API call nobody would think to make.
        #
        # `DEFAULT_UNITS` is imported from `domain/catalog/units.py` — the same tuple
        # migration 0016 and the service both read, so a business provisioned here can
        # never get a different set from one created either other way.
        #
        # The ids are `uuid5` under migration 0016's own namespace, so if 0016 is ever
        # re-run against this database its `ON CONFLICT (business_id, code) DO UPDATE`
        # matches these rows instead of creating duplicates.
        #
        # This is reference data, which `DATA-MODEL.md §6` rule 7 permits alongside
        # permissions and system roles. It is not business data: no product, no price, no
        # stock figure. `allows_fraction` is the field that carries the weight — a SET of
        # brake shoes cannot be sold in halves, oil by the litre can (E-18).
        from bm.domain.catalog.units import DEFAULT_UNITS

        for code, unit_name, allows_fraction, places in DEFAULT_UNITS:
            connection.execute(
                text(
                    "INSERT INTO units (id, business_id, code, name, allows_fraction,"
                    " decimal_places) VALUES (:id, :b, :code, :name, :frac, :places)"
                    " ON CONFLICT (business_id, code) DO NOTHING"
                ),
                {
                    "id": uuid.uuid5(_UNIT_NAMESPACE, f"{business_id}:{code}"),
                    "b": business_id,
                    "code": code,
                    "name": unit_name,
                    "frac": allows_fraction,
                    "places": places,
                },
            )

        connection.commit()

    print("provisioned.")
    print()
    print("Record these — the ids are needed when registering the counter PC:")
    print(f"  business_id  {business_id}")
    print(f"  branch_id    {branch_id}")
    print(f"  counter_id   {counter_id}")
    print(f"  username     {args.owner_username}")
    print()
    if args.force_password_change:
        print("The owner MUST change this password at first sign-in: `must_change_password`")
        print("blocks every non-auth endpoint until they do (`api/deps.py`), and the counter")
        print("application renders the change-password screen for it.")
    print()
    print("Still to do, and NOT done by this tool: products, opening stock, customers and")
    print("prices. Load those through the application or the import screens so every stock")
    print("figure arrives as an `opening` movement with an audit trail.")
    return 0


# --- cli ---------------------------------------------------------------------


def _add_target_arguments(parser: argparse.ArgumentParser, *, default_variable: str) -> None:
    parser.add_argument(
        "--variable",
        default=default_variable,
        help=f"environment variable holding the connection string (default {default_variable})",
    )
    parser.add_argument(
        "--expect-database",
        default="neondb",
        help="the database name the URL must name (default neondb)",
    )
    parser.add_argument(
        "--expect-host-suffix",
        default="neon.tech",
        help="the host suffix the URL must end with (default neon.tech)",
    )
    parser.add_argument(
        "--allow-any-host",
        action="store_true",
        help="skip the host-suffix check (for a non-Neon production database)",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="production_db.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subcommands = parser.add_subparsers(dest="command", required=True)

    inspect = subcommands.add_parser(
        "inspect", help="read-only report on the production database and whether it is fresh"
    )
    _add_target_arguments(inspect, default_variable="BM_MIGRATION_DATABASE_URL")
    inspect.set_defaults(handler=cmd_inspect)

    create_role = subcommands.add_parser(
        "create-app-role",
        help="run neon-init-roles.sql, verify bm_app, and repoint BM_DATABASE_URL at it",
    )
    _add_target_arguments(create_role, default_variable="BM_MIGRATION_DATABASE_URL")
    create_role.add_argument(
        "--yes", action="store_true", help="actually create the role and rewrite .env.production"
    )
    create_role.set_defaults(handler=cmd_create_app_role)

    migrate = subcommands.add_parser(
        "migrate", help="alembic upgrade head, after a freshness pre-flight"
    )
    _add_target_arguments(migrate, default_variable="BM_MIGRATION_DATABASE_URL")
    migrate.add_argument("--yes", action="store_true", help="actually apply the migrations")
    migrate.add_argument(
        "--allow-missing-app-role",
        action="store_true",
        help="migrate without bm_app, accepting that append-only grants will be skipped",
    )
    migrate.set_defaults(handler=cmd_migrate)

    verify = subcommands.add_parser(
        "verify", help="assert the schema, privileges and emptiness of the database"
    )
    _add_target_arguments(verify, default_variable="BM_MIGRATION_DATABASE_URL")
    verify.add_argument(
        "--skip-runtime-check",
        action="store_true",
        help="do not connect as bm_app (use when only BM_MIGRATION_DATABASE_URL is available)",
    )
    verify.set_defaults(handler=cmd_verify)

    zero_state = subcommands.add_parser(
        "zero-state", help="per-table row counts with expected values (read-only audit)"
    )
    _add_target_arguments(zero_state, default_variable="BM_MIGRATION_DATABASE_URL")
    zero_state.add_argument(
        "--markdown", action="store_true", help="emit a markdown table for the readiness report"
    )
    zero_state.set_defaults(handler=cmd_zero_state)

    provision = subcommands.add_parser(
        "provision", help="create the business, branch, counter and owner account"
    )
    _add_target_arguments(provision, default_variable="BM_MIGRATION_DATABASE_URL")
    provision.add_argument("--yes", action="store_true", help="actually write the rows")
    provision.add_argument("--dry-run", action="store_true", help="show what would be written")
    provision.add_argument("--display-name", default=DEFAULT_DISPLAY_NAME)
    provision.add_argument(
        "--address-line",
        action="append",
        default=None,
        help=f"repeatable; defaults to {DEFAULT_ADDRESS_LINES}",
    )
    provision.add_argument(
        "--phone",
        action="append",
        default=None,
        help=f"repeatable; defaults to {DEFAULT_PHONE_NUMBERS}",
    )
    provision.add_argument("--timezone", default=DEFAULT_TIMEZONE)
    provision.add_argument("--fy-start-month", type=int, default=DEFAULT_FY_START_MONTH)
    provision.add_argument("--branch-code", default="MAIN")
    provision.add_argument("--branch-name", default="Main")
    provision.add_argument("--counter-code", default="C1")
    provision.add_argument("--counter-name", default="Counter 1")
    # C-5 makes 20 a floor, not a default (`domain/settings/defaults.py`).
    provision.add_argument("--max-open-drafts", type=int, default=20)
    provision.add_argument("--owner-username", default="owner")
    provision.add_argument(
        "--owner-full-name",
        default="Owner",
        help="the owner's personal name is not in the workbook, so this defaults to the role",
    )
    provision.add_argument(
        "--no-force-password-change",
        dest="force_password_change",
        action="store_false",
        help="do not require a password change at first sign-in (not recommended)",
    )
    provision.set_defaults(handler=cmd_provision, force_password_change=True)

    return parser


def _force_utf8_output() -> None:
    """A Windows console defaults to the legacy ANSI codepage.

    That renders the section marks and em dashes in this tool's output as `?`, which is
    cosmetic in a test and not in a runbook: these messages name the host about to be
    written to and the reason a write was refused, and an operator should not have to
    wonder whether a mangled character changed the meaning. `errors="replace"` so a
    console that still cannot encode something substitutes rather than raising halfway
    through a report.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


def main(argv: list[str] | None = None) -> int:
    _force_utf8_output()
    args = build_parser().parse_args(argv)

    if args.command == "provision":
        if args.address_line is None:
            args.address_line = list(DEFAULT_ADDRESS_LINES)
        if args.phone is None:
            args.phone = list(DEFAULT_PHONE_NUMBERS)

    try:
        handler: object = args.handler
        assert callable(handler)
        return int(handler(args))
    except DBAPIError as exc:
        # Reported rather than raised: a traceback through psycopg can carry the
        # connection string, and that string is a production credential.
        print(f"\nDATABASE ERROR: {type(exc.orig).__name__}: {exc.orig}\n", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
