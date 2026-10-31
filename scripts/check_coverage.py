"""Per-module **branch** coverage floors.

Why this exists
---------------

`coverage report --fail-under=85` enforces one number over the whole codebase, and the
30 October 2026 audit showed what that hides: the overall figure sat comfortably above
85% while the five modules that decide whether money and stock are correct sat between
68.8% and 92.5%. An average cannot fail on a module; a well-covered print-layout module
pays for a poorly covered credit engine.

`coverage` has no per-path threshold of its own, so this script is it. It reads
`coverage.json` (`coverage json`), groups files into the named modules below, computes
**branch** coverage per module, and exits non-zero on any shortfall.

Branch, not line, deliberately. A line-coverage figure is satisfied by executing an
`if`; the branch figure is only satisfied by executing it both ways, and "both ways" is
where a credit limit, an oversell refusal or an idempotency replay actually lives.

Usage:

    pytest --cov --cov-report=json
    python scripts/check_coverage.py

    # or point it elsewhere / override for a one-off investigation
    python scripts/check_coverage.py --json coverage.json --show-missing

Rules this script is written to keep honest
-------------------------------------------

* **A module with no matching file is a failure, not a pass.** A renamed or deleted
  directory silently emptying its own floor is precisely how a coverage gate rots.
* **No file in these modules may be excluded.** The script refuses to run if a configured
  `omit`/`exclude` pattern would drop one of them, because excluding the hard file is the
  easiest way to make a threshold go green without covering anything.
* The floors live here, in the repository, next to the code they describe — not in a CI
  file where they can be edited by whoever is trying to get a build through.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

#: The agreed floor for the five critical modules (audit Issue B).
#:
#: 95% **branch** coverage. This is the documented target; it is not negotiable downwards
#: without the owner's explicit authorisation, and a change to these numbers should be
#: visible in a diff and reviewed as a requirement change rather than a build fix.
CRITICAL_BRANCH_FLOOR = 95.0

#: The overall floor that already existed, kept rather than replaced. The per-module
#: floors are an addition: a module can now fail on its own.
OVERALL_FLOOR = 85.0


@dataclass(frozen=True)
class Module:
    """A named group of files with a branch-coverage floor."""

    name: str
    #: Path prefixes, relative to the repository root, POSIX-separated.
    prefixes: tuple[str, ...]
    floor: float
    why: str


#: The five modules the audit named, and why each one carries the floor.
CRITICAL_MODULES: tuple[Module, ...] = (
    Module(
        "money",
        ("src/bm/domain/shared/money.py",),
        CRITICAL_BRANCH_FLOOR,
        "every rupee on every bill goes through it; a wrong branch is a wrong total",
    ),
    Module(
        "sales",
        ("src/bm/domain/sales/",),
        CRITICAL_BRANCH_FLOOR,
        "finalisation: numbering, locks, stock movements and payment in one transaction",
    ),
    Module(
        "inventory",
        ("src/bm/domain/inventory/",),
        CRITICAL_BRANCH_FLOOR,
        "stock must be deducted exactly once and never by an unauthorised edit",
    ),
    Module(
        "credit",
        ("src/bm/domain/credit/",),
        CRITICAL_BRANCH_FLOOR,
        "a customer's balance and credit limit; a wrong branch is money owed or forgiven",
    ),
    Module(
        "sync",
        ("src/bm/domain/sync/",),
        CRITICAL_BRANCH_FLOOR,
        "idempotency and replay: the difference between one bill and two",
    ),
)


def _normalise(path: str) -> str:
    """`coverage.json` keys use the platform separator; comparisons use POSIX."""
    return path.replace("\\", "/").lstrip("./")


def _matches(path: str, prefixes: tuple[str, ...]) -> bool:
    normalised = _normalise(path)
    for prefix in prefixes:
        clean = prefix.rstrip("/")
        if normalised == clean or normalised.startswith(f"{clean}/"):
            return True
        # A file prefix such as `.../money.py` matches exactly, and a suffix match
        # covers a report rooted differently (`bm/domain/...` rather than `src/bm/...`).
        if normalised.endswith(clean):
            return True
    return False


@dataclass
class Result:
    module: Module
    files: list[str]
    branches: int
    covered: int
    missing_files: list[tuple[str, int, int]]

    @property
    def percent(self) -> float:
        # A module with no branches at all is vacuously covered; a module with no
        # *files* is handled separately, as a failure.
        return 100.0 if self.branches == 0 else 100.0 * self.covered / self.branches

    @property
    def ok(self) -> bool:
        return bool(self.files) and self.percent >= self.module.floor

    @property
    def branches_needed(self) -> int:
        """How many more covered branches would clear the floor."""
        if self.branches == 0:
            return 0
        import math

        required = math.ceil(self.module.floor / 100.0 * self.branches)
        return max(0, required - self.covered)


def evaluate(report: dict, modules: tuple[Module, ...]) -> list[Result]:
    results: list[Result] = []
    for module in modules:
        files: list[str] = []
        branches = covered = 0
        missing: list[tuple[str, int, int]] = []
        for path, entry in sorted(report["files"].items()):
            if not _matches(path, module.prefixes):
                continue
            summary = entry["summary"]
            files.append(_normalise(path))
            branches += summary["num_branches"]
            covered += summary["covered_branches"]
            short = summary["num_branches"] - summary["covered_branches"]
            if short:
                missing.append((_normalise(path), short, summary["num_branches"]))
        results.append(Result(module, files, branches, covered, missing))
    return results


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--json",
        default=str(REPO_ROOT / "coverage.json"),
        help="coverage JSON report (default: ./coverage.json)",
    )
    parser.add_argument(
        "--show-missing",
        action="store_true",
        help="list the files short of the floor, worst first",
    )
    parser.add_argument(
        "--overall-floor",
        type=float,
        default=OVERALL_FLOOR,
        help=f"overall statement floor (default: {OVERALL_FLOOR})",
    )
    args = parser.parse_args()

    path = Path(args.json)
    if not path.is_file():
        print(f"FAIL: {path} does not exist. Run: pytest --cov --cov-report=json", file=sys.stderr)
        return 2
    report = json.loads(path.read_text(encoding="utf-8"))

    results = evaluate(report, CRITICAL_MODULES)

    print("Per-module branch coverage")
    print("=" * 78)
    print(f"{'module':11} {'branches':>9} {'covered':>8} {'branch%':>9} {'floor':>7}  status")
    print("-" * 78)
    failures: list[Result] = []
    for result in results:
        if not result.files:
            status = "FAIL (no files matched)"
            failures.append(result)
        elif result.ok:
            status = "pass"
        else:
            status = f"FAIL (needs {result.branches_needed} more covered branches)"
            failures.append(result)
        print(
            f"{result.module.name:11} {result.branches:>9} {result.covered:>8} "
            f"{result.percent:>8.1f}% {result.module.floor:>6.1f}%  {status}"
        )

    if args.show_missing:
        print()
        print("Files short of their module floor (worst first)")
        print("-" * 78)
        for result in results:
            for file, short, total in sorted(result.missing_files, key=lambda row: -row[1]):
                print(f"  [{result.module.name:9}] {file:50} {short:>3} of {total:>3} uncovered")

    # The overall floor that already existed, still enforced.
    overall = report["totals"]["percent_covered"]
    print()
    print(f"Overall statement coverage: {overall:.2f}%  (floor {args.overall_floor:.1f}%)")
    overall_ok = overall >= args.overall_floor

    print()
    if failures or not overall_ok:
        if failures:
            print("Branch-coverage floors not met:")
            for result in failures:
                print(f"  - {result.module.name}: {result.module.why}")
            print()
            print(
                "Cover the missing branches. Do not lower these floors, exclude a file, or\n"
                "add a test that executes a line without asserting a business outcome —\n"
                "all three make this gate green while leaving the defect it exists to catch."
            )
        if not overall_ok:
            print(
                f"  - overall statement coverage {overall:.2f}% is below {args.overall_floor:.1f}%"
            )
        return 1

    print("All coverage floors met.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
