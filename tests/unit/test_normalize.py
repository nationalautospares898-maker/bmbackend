"""Normalisation, against the **shared fixture list**.

`PART_NUMBER_SPELLINGS` below is the same list `bmfrontend/src/lib/normalize.test.ts`
uses, and that is the point of it.

Normalisation is **deliberately duplicated** between the server and the client
(`../bmfrontend/docs/ARCHITECTURE.md §8.3`): offline search runs with no server, so the
rules have to exist on both sides. A duplicated rule is a rule that drifts, and the
failure mode is specific and bad — a cashier offline cannot find a part they can see on
the shelf, or finds a different one than the same typing would find online.

The mitigation is this list. One small pure module on each side, one shared set of real
part-number spellings, exercised by both suites. If this list changes, the TypeScript
copy changes in the same change set.
"""

from __future__ import annotations

import pytest

from bm.domain.shared.normalize import (
    normalize_name,
    normalize_phone,
    normalize_sku,
    normalize_vehicle_number,
)

#: Real part-number spellings a counter hand types, grouped by the product they mean.
#: Shared with `bmfrontend/src/lib/normalize.test.ts`.
PART_NUMBER_SPELLINGS: tuple[tuple[str, ...], ...] = (
    ("TT-407-BS", "tt407bs", "tt 407 bs", "TT.407.BS", "tt/407/bs", "Tt-407-Bs"),
    ("MB-CP-01", "mbcp01", "mb cp 01", "MB.CP.01"),
    ("6204-2RS", "62042rs", "6204 2rs", "6204/2RS"),
    ("HF-3509", "hf3509", "hf 3509", "HF.3509"),
)


@pytest.mark.parametrize("spellings", PART_NUMBER_SPELLINGS, ids=lambda s: s[0])
def test_every_spelling_of_one_part_code_is_one_key(spellings: tuple[str, ...]) -> None:
    """FR-CAT-006. Nobody types a part number the way it is printed."""
    keys = {normalize_sku(spelling) for spelling in spellings}
    assert len(keys) == 1, f"{spellings} produced {keys}"


def test_genuinely_different_codes_stay_different() -> None:
    """The rule removes separators; it must not remove information.

    `TT-407` and `TT-408` are different parts, and a cashier picking the wrong one means
    the wrong part fitted to a customer's vehicle.
    """
    assert normalize_sku("TT-407-BS") != normalize_sku("TT-408-BS")
    assert normalize_sku("6204") != normalize_sku("6205")


def test_accents_are_stripped_so_both_unicode_forms_match() -> None:
    assert normalize_sku("DÜRR-12") == normalize_sku("DURR-12")


def test_input_with_no_alphanumerics_produces_no_key() -> None:
    """`None`, not `""`, and the difference from the client is deliberate.

    Here the value is a **stored column** under a partial unique index, which has to
    permit many products with no part code — so "no code" must be `NULL` rather than a
    shared empty string. In the client it is only ever a search key, so it is `""`
    there, and `rankOffline` checks for the empty string before comparing.

    Recorded in both suites rather than silently differing, because this is exactly the
    kind of gap the shared fixture list exists to make visible.
    """
    assert normalize_sku("---") is None


def test_a_name_keeps_the_spaces_that_make_it_readable() -> None:
    """Deliberately different from `normalize_sku`.

    `brakeshoeset` would make a prefix search on "brake shoe" impossible, and a name is
    read by a human rather than compared as a code.
    """
    assert normalize_name("  Brake   Shoe  Set ") == "brake shoe set"
    assert normalize_name("Brake Shoe Set") != "brakeshoeset"


def test_phone_numbers_compare_by_their_last_ten_digits() -> None:
    """One number however it was written (FR-CUST-004).

    Keeping the last ten drops a country code or a trunk `0` without having to work out
    which is which. The client implements the same rule; a client that kept the `91`
    would file the same customer twice — once from a bill typed offline and once from a
    bill typed online.
    """
    for written in ("9876543210", "98765 43210", "(98765) 43210", "+91-9876543210", "098765 43210"):
        assert normalize_phone(written) == "9876543210", written


def test_vehicle_numbers_compare_however_they_are_written() -> None:
    forms = ("KA 01 AB 1234", "ka-01-ab-1234", "KA01AB1234")
    assert len({normalize_vehicle_number(form) for form in forms}) == 1
