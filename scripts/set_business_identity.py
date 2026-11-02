"""Put the shop's real identity on the business record, replacing the demo placeholder.

## Why this exists

`seed_dev.py` deliberately wrote `DEMO — NOT A REAL BUSINESS` with an empty address and
no phone numbers. That was correct at the time and is documented in its own module
docstring: OD-4 was unresolved, and `CLAUDE.md §12` forbids inventing business identity,
because a plausible placeholder in a seeded database is exactly how a placeholder ends up
printed on a real customer's bill.

The owner has now supplied the identity themselves, in the estimate workbook this project
was built from — `ANAS_1234_20260606_231456.xlsx`. Rows 1 to 3 of `Sheet1` are the bill's
own letterhead:

    A1  NATIONAL  AUTO SPARES
    A2  AUTO NAGAR ,HYDERABAD
    A3  Mob:9948207903,9848136564

So nothing here is invented or guessed: every value below is read out of the owner's file,
which is the authority OD-4 was waiting for. The values are quoted in the constants so a
reviewer can check them against the workbook without running anything.

## What it deliberately does not do

* **`legal_name` stays NULL.** The workbook shows a trading name, not a registered legal
  name, and the two are not the same thing. OD-4 remains open for it, it is a go-live
  gate rather than a display problem, and guessing it would be the precise mistake this
  script's existence is a reaction to. Same for `tax_identifier`: no GSTIN appears in the
  workbook and `tax.mode` is still `estimate_only`.
* **It touches nothing but identity.** No invoice, payment, stock movement, customer,
  product, counter or audit row is read or written. The `UPDATE` names its columns.
* **It never creates anything.** If the business row is missing it says so and stops,
  because creating a business is a provisioning decision.

## Safety

Idempotent: running it twice is a no-op the second time, and it prints the before and
after values either way. It takes the database from `BM_DATABASE_URL` and **prints which
database it is about to write to**, because the whole point is that this one is meant to
run against the owner's real `bm` — unlike every other script here, it has no scratch-only
guard to lean on, so the operator gets to see the target before anything happens.

    uv run python scripts/set_business_identity.py --dry-run
    uv run python scripts/set_business_identity.py
"""

from __future__ import annotations

import argparse
import sys

from sqlalchemy import create_engine, text

from bm.config import Settings

# --- the owner's own letterhead, from `ANAS_1234_20260606_231456.xlsx` ---------

#: `A1`, with the workbook's double space collapsed. The workbook reads
#: `NATIONAL  AUTO SPARES`; the doubled space is a typing artefact of a spreadsheet cell,
#: not part of the name, and it would render as a visible gap in the bill's heading.
DISPLAY_NAME = "NATIONAL AUTO SPARES"

#: `A2`, with the stray space before the comma tidied. Same reasoning as the name: the
#: place is "Auto Nagar, Hyderabad" and `AUTO NAGAR ,HYDERABAD` is how it was typed.
ADDRESS_LINES = ["AUTO NAGAR, HYDERABAD"]

#: `A3`, split into the two numbers it contains. The `Mob:` prefix is a **label**, not
#: part of a phone number, and the print layout joins this list with `, ` to render the
#: contact line — so storing the label inside a number would put it in the middle of the
#: printed line on the second one.
PHONE_NUMBERS = ["9948207903", "9848136564"]

#: The owner's display name. Their personal name is **not** in the workbook, so this is
#: deliberately the role rather than a guess at a person: it removes `Demo Owner` from the
#: screen without inventing somebody. The owner can set their real name later.
OWNER_FULL_NAME = "Owner"

#: `demo-owner` is a credential rather than branding — the application never displays it —
#: but a real shop's only login should not be called `demo-anything`, and the owner reads
#: it every morning. The password is **not** touched: changing a credential nobody asked
#: to change is its own hazard, and the operator is told to change it themselves.
OWNER_USERNAME = "owner"
PREVIOUS_USERNAME = "demo-owner"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run", action="store_true", help="show what would change and write nothing"
    )
    args = parser.parse_args()

    url = Settings().database_url
    # Printed without the password, which `repr` of a SQLAlchemy URL masks for us.
    engine = create_engine(url, future=True)
    print(f"database  {engine.url.render_as_string(hide_password=True)}")
    print(f"mode      {'dry run — nothing will be written' if args.dry_run else 'writing'}\n")

    with engine.begin() as connection:
        business = connection.execute(
            text(
                "SELECT id, display_name, legal_name, address_lines, phone_numbers"
                " FROM businesses ORDER BY created_at LIMIT 1"
            )
        ).first()
        if business is None:
            print("No business row found. Nothing to do — provisioning is a separate step.")
            return 1

        print("before")
        print(f"  display_name   {business.display_name!r}")
        print(f"  legal_name     {business.legal_name!r}")
        print(f"  address_lines  {list(business.address_lines)!r}")
        print(f"  phone_numbers  {list(business.phone_numbers)!r}")

        users = connection.execute(
            text(
                "SELECT id, username, full_name FROM users"
                " WHERE business_id = :b ORDER BY created_at"
            ),
            {"b": business.id},
        ).all()
        for user in users:
            print(f"  user           {user.username!r} / {user.full_name!r}")

        if args.dry_run:
            print("\nafter (would be)")
            print(f"  display_name   {DISPLAY_NAME!r}")
            print("  legal_name     None  (left as-is: not in the workbook, OD-4 still open)")
            print(f"  address_lines  {ADDRESS_LINES!r}")
            print(f"  phone_numbers  {PHONE_NUMBERS!r}")
            print(f"  owner          {OWNER_USERNAME!r} / {OWNER_FULL_NAME!r}")
            return 0

        connection.execute(
            text(
                "UPDATE businesses SET display_name = :name, address_lines = :address,"
                " phone_numbers = :phones WHERE id = :id"
            ),
            {
                "name": DISPLAY_NAME,
                "address": ADDRESS_LINES,
                "phones": PHONE_NUMBERS,
                "id": business.id,
            },
        )

        # Scoped to the placeholder spellings on purpose. A name somebody has since set
        # for themselves is not a placeholder and is left exactly alone.
        connection.execute(
            text(
                "UPDATE users SET full_name = :full_name"
                " WHERE business_id = :b AND full_name IN ('Demo Owner', 'Demo owner')"
            ),
            {"full_name": OWNER_FULL_NAME, "b": business.id},
        )
        connection.execute(
            text(
                "UPDATE users SET username = :new WHERE business_id = :b AND username = :old"
                # Belt and braces against a second run or a half-finished first one: if
                # `owner` somehow already exists, the unique index would reject this and
                # the whole transaction would roll back rather than half-apply.
                " AND NOT EXISTS (SELECT 1 FROM users WHERE business_id = :b"
                " AND username = :new)"
            ),
            {"new": OWNER_USERNAME, "old": PREVIOUS_USERNAME, "b": business.id},
        )

        after = connection.execute(
            text(
                "SELECT display_name, legal_name, address_lines, phone_numbers"
                " FROM businesses WHERE id = :id"
            ),
            {"id": business.id},
        ).one()
        users_after = connection.execute(
            text("SELECT username, full_name FROM users WHERE business_id = :b"),
            {"b": business.id},
        ).all()

    print("\nafter")
    print(f"  display_name   {after.display_name!r}")
    print(f"  legal_name     {after.legal_name!r}")
    print(f"  address_lines  {list(after.address_lines)!r}")
    print(f"  phone_numbers  {list(after.phone_numbers)!r}")
    for user in users_after:
        print(f"  user           {user.username!r} / {user.full_name!r}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
