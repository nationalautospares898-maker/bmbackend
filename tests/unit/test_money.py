"""M4 — the money engine.

Every assertion traces to §3 of the Design Completion Addendum. The tests are grouped by
the five concerns that document separates, because conflating them is what produced the
original ambiguity: **input validation**, **calculation precision**, **stored values**,
**rounding**, and **display**.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from bm.domain.shared.errors import EmptySale, TotalMismatch, ValidationFailed
from bm.domain.shared.money import (
    DEFAULT_POLICY,
    InvoiceDiscount,
    LineInput,
    MoneyPolicy,
    RoundingMode,
    amount_in_words,
    assert_client_total_matches,
    assert_integral_amount,
    assert_scale_within,
    compute_invoice_totals,
    compute_line,
    format_for_display,
    format_quantity,
    is_integral,
    parse_decimal,
)
from bm.domain.shared.quantity import UnitSpec, validate_quantity

D = Decimal


def _line(quantity: str, unit_price: str, **kwargs: str) -> LineInput:
    return LineInput(
        quantity=D(quantity),
        unit_price=D(unit_price),
        discount_percent=D(kwargs.get("discount_percent", "0")),
        discount_amount=D(kwargs.get("discount_amount", "0")),
        tax_rate_percent=D(kwargs.get("tax_rate_percent", "0")),
    )


# =============================================================================
# 1. Input validation (addendum §3.1)
# =============================================================================


def test_decimal_strings_are_accepted() -> None:
    assert parse_decimal("1250.00", field_name="unit_price") == D("1250.00")


def test_integers_are_accepted_because_they_cannot_lose_precision() -> None:
    assert parse_decimal(1250, field_name="unit_price") == D(1250)


def test_floats_are_rejected_not_coerced() -> None:
    """A float has already lost precision before arriving; accepting one would mean
    accepting a value nobody can audit."""
    with pytest.raises(ValidationFailed) as caught:
        parse_decimal(1250.5, field_name="unit_price")
    assert "lose precision" in caught.value.details["fields"]["unit_price"]


def test_booleans_are_rejected() -> None:
    with pytest.raises(ValidationFailed):
        parse_decimal(True, field_name="unit_price")


@pytest.mark.parametrize("value", ["1e3", "1E3", "Infinity", "-Infinity", "NaN", "nan"])
def test_exponent_and_non_finite_notation_is_rejected(value: str) -> None:
    """`1e3` is numerically 1000 but its precision is ambiguous."""
    with pytest.raises(ValidationFailed):
        parse_decimal(value, field_name="unit_price")


@pytest.mark.parametrize("value", ["", "   ", "abc", "12.3.4", "1,250.00", None, [], {}])
def test_malformed_values_are_rejected(value: object) -> None:
    with pytest.raises(ValidationFailed):
        parse_decimal(value, field_name="unit_price")


def test_excess_precision_is_rejected_not_silently_rounded() -> None:
    """`SECURITY.md §4`: too many decimal places is a 422, never a quiet truncation —
    the caller meant something, and guessing which part to discard is how a discount
    disappears."""
    with pytest.raises(ValidationFailed) as caught:
        assert_scale_within(D("10.12345"), max_places=4, field_name="unit_price")
    assert "4 decimal places" in caught.value.details["fields"]["unit_price"]


def test_scale_within_limit_passes_unchanged() -> None:
    assert assert_scale_within(D("10.1234"), max_places=4, field_name="p") == D("10.1234")


@pytest.mark.parametrize("value", ["0", "1", "99", "1250", "-0"])
def test_integral_amounts_are_accepted(value: str) -> None:
    assert_integral_amount(D(value), field_name="amount", policy=DEFAULT_POLICY)


@pytest.mark.parametrize("value", ["0.01", "1250.50", "99.99", "0.5"])
def test_fractional_amounts_are_rejected_under_the_v1_policy(value: str) -> None:
    """The whole-rupee rule. A fractional monetary value is refused **before**
    finalisation — the only way a zero-decimal bill can be both faithful to the owner's
    format and honest about the amount."""
    with pytest.raises(ValidationFailed) as caught:
        assert_integral_amount(D(value), field_name="amount", policy=DEFAULT_POLICY)
    message = caught.value.details["fields"]["amount"]
    assert "whole rupees" in message
    assert "not be rounded" in message


def test_integral_rule_can_be_relaxed_by_policy() -> None:
    """The engine is policy-parameterised, not hardcoded to the v1 values (M4)."""
    relaxed = MoneyPolicy(version=99, require_integral_amounts=False)
    assert_integral_amount(D("1250.50"), field_name="amount", policy=relaxed)


# =============================================================================
# 2. Calculation precision (addendum §3.2)
# =============================================================================


def test_line_extension_is_exact() -> None:
    totals = compute_line(_line("2.000", "1250.0000"))
    assert totals.taxable_amount == D("2500.00")
    assert totals.line_total == D("2500.00")


def test_fractional_quantity_with_a_whole_rupee_result_is_accepted() -> None:
    """The whole-rupee rule applies to the **extension**, not the quantity: half a
    litre at 240 is 120, which is a perfectly printable figure."""
    totals = compute_line(_line("0.500", "240.0000"))
    assert totals.taxable_amount == D("120.00")


def test_fractional_quantity_producing_paise_is_rejected() -> None:
    with pytest.raises(ValidationFailed) as caught:
        compute_line(_line("0.500", "241.0000"))
    assert "lines.1.line_total" in caught.value.details["fields"]


def test_percentage_discount_is_applied_in_exact_decimal() -> None:
    totals = compute_line(_line("1.000", "1000.0000", discount_percent="10.000"))
    assert totals.discount_total == D("100.00")
    assert totals.taxable_amount == D("900.00")


def test_monetary_discount_is_applied_after_the_percentage_one() -> None:
    """Order is fixed: applying the monetary discount first would make the percentage
    mean something different, and two code paths disagreeing about that is a bug that
    only surfaces in reconciliation months later."""
    totals = compute_line(
        _line("1.000", "1000.0000", discount_percent="10.000", discount_amount="50")
    )
    # 10% of 1000 = 100, then 50 → 150 total discount, 850 net.
    assert totals.discount_total == D("150.00")
    assert totals.taxable_amount == D("850.00")


def test_discount_larger_than_the_line_is_rejected() -> None:
    with pytest.raises(ValidationFailed) as caught:
        compute_line(_line("1.000", "100.0000", discount_amount="150"))
    assert "lines.1.discount" in caught.value.details["fields"]


def test_no_float_appears_anywhere_in_the_result() -> None:
    """ADR-013. A single float would corrupt a total silently and permanently."""
    totals = compute_invoice_totals([_line("3.000", "333.0000")])
    for value in (
        totals.subtotal,
        totals.discount_total,
        totals.tax_total,
        totals.rounding_adjustment,
        totals.total,
    ):
        assert isinstance(value, Decimal)
    for line in totals.lines:
        for value in (line.gross, line.taxable_amount, line.tax_amount, line.line_total):
            assert isinstance(value, Decimal)


def test_a_value_binary_floating_point_cannot_represent_stays_exact() -> None:
    """`0.1 + 0.2 != 0.3` in IEEE-754. Here it must."""
    totals = compute_invoice_totals(
        [_line("1.000", "10.0000"), _line("1.000", "20.0000"), _line("1.000", "30.0000")]
    )
    assert totals.total == D("60.00")


def test_large_invoice_sums_exactly() -> None:
    """A 25-line bill is the workbook's capacity; errors accumulate in sums, so the
    identity is checked at full size."""
    lines = [_line("2.000", "1234.0000") for _ in range(25)]
    totals = compute_invoice_totals(lines)
    assert totals.subtotal == D("61700.00")  # 25 × 2 × 1234
    assert totals.total == D("61700.00")
    assert totals.reconciles()


# =============================================================================
# 3. The reconciliation identity (NFR-DATA-002) — the invariant that matters most
# =============================================================================

ALL_POLICIES = [
    MoneyPolicy(version=1),  # v1 default: reject fractional, no rounding
    MoneyPolicy(version=2, rounding_mode=RoundingMode.LINE_HALF_UP, require_integral_amounts=False),
    MoneyPolicy(
        version=4,
        rounding_mode=RoundingMode.LINE_HALF_UP,
        round_grand_total_to_rupee=True,
        require_integral_amounts=False,
    ),
]

#: Line sets chosen to include awkward values: thirds, repeating decimals, paise.
LINE_SETS = [
    [_line("1.000", "1250.0000")],
    [_line("3.000", "333.3300")],
    [_line("1.000", "0.0100")],
    [_line("7.000", "142.8571")],
    [_line("2.500", "99.9900")],
    [_line("1.000", "1000.0000", discount_percent="33.333")],
    [_line("1.000", "100.0000", tax_rate_percent="18.000")],
    [_line("1.000", "10.0000"), _line("2.000", "20.0000"), _line("3.000", "30.0000")],
    [_line("0.333", "3.0000")],
]


@pytest.mark.parametrize("policy", ALL_POLICIES, ids=lambda p: f"policy{p.version}")
@pytest.mark.parametrize("lines", LINE_SETS, ids=lambda ls: f"{len(ls)}-lines")
def test_totals_reconcile_for_every_policy_combination(
    policy: MoneyPolicy, lines: list[LineInput]
) -> None:
    """M4's acceptance criterion: correct **for every policy combination**, not just
    the proposed default.

    Under the v1 policy a fractional result is a `ValidationFailed` — which is the
    documented, correct outcome, not a test failure. What must never happen is a
    committed total that does not reconcile.
    """
    try:
        totals = compute_invoice_totals(lines, policy=policy)
    except ValidationFailed:
        assert policy.rounding_mode is RoundingMode.REJECT_FRACTIONAL
        return
    assert totals.reconciles(), (
        f"total={totals.total} subtotal={totals.subtotal} "
        f"discount={totals.discount_total} tax={totals.tax_total} "
        f"rounding={totals.rounding_adjustment}"
    )


@pytest.mark.parametrize("policy", ALL_POLICIES, ids=lambda p: f"policy{p.version}")
def test_subtotal_equals_the_sum_of_line_extensions(policy: MoneyPolicy) -> None:
    """NFR-DATA-002b. The invoice total must never be an independently computed figure
    that could drift from the rows meant to explain it."""
    lines = [_line("2.000", "100.0000"), _line("1.000", "250.0000")]
    totals = compute_invoice_totals(lines, policy=policy)
    assert totals.subtotal == sum(line.taxable_amount for line in totals.lines)


def test_whole_rupee_rounding_records_a_visible_adjustment() -> None:
    """Addendum §3.4: if rounding is ever authorised it must be recorded in its own
    column so the arithmetic still reconciles and the rounding is visible rather than
    concealed inside the total."""
    policy = MoneyPolicy(
        version=4,
        rounding_mode=RoundingMode.LINE_HALF_UP,
        round_grand_total_to_rupee=True,
        require_integral_amounts=False,
    )
    totals = compute_invoice_totals([_line("1.000", "99.4900")], policy=policy)
    assert totals.total == D("99.00")
    assert totals.rounding_adjustment == D("-0.49")
    assert totals.reconciles()


def test_v1_never_produces_a_rounding_adjustment() -> None:
    """Addendum §3.4: no rounding is performed in v1; the adjustment is exactly zero."""
    totals = compute_invoice_totals([_line("2.000", "1250.0000")])
    assert totals.rounding_adjustment == D("0.00")


def test_empty_sale_is_rejected() -> None:
    with pytest.raises(EmptySale):
        compute_invoice_totals([])


def test_invoice_discount_larger_than_the_bill_is_rejected() -> None:
    with pytest.raises(ValidationFailed):
        compute_invoice_totals(
            [_line("1.000", "100.0000")], invoice_discount=InvoiceDiscount(amount=D("150"))
        )


def test_invoice_level_discount_reconciles() -> None:
    totals = compute_invoice_totals(
        [_line("1.000", "1000.0000")], invoice_discount=InvoiceDiscount(amount=D("100"))
    )
    assert totals.subtotal == D("1000.00")
    assert totals.discount_total == D("100.00")
    assert totals.total == D("900.00")
    assert totals.reconciles()


def test_line_discounts_are_not_double_counted() -> None:
    """They are already reflected in each line's net extension and therefore in
    `subtotal`; adding them to `discount_total` would break `ck_invoices__total`."""
    totals = compute_invoice_totals([_line("1.000", "1000.0000", discount_amount="100")])
    assert totals.subtotal == D("900.00")
    assert totals.discount_total == D("0.00")
    assert totals.line_discount_total == D("100.00")
    assert totals.total == D("900.00")
    assert totals.reconciles()


# =============================================================================
# 4. The client cross-check (FR-SALE-003)
# =============================================================================


def test_matching_client_total_passes() -> None:
    assert_client_total_matches(D("2500.00"), D("2500.00"))


def test_absent_client_total_is_not_an_error() -> None:
    assert_client_total_matches(D("2500.00"), None)


def test_mismatched_client_total_is_never_silently_overridden() -> None:
    """A mismatch means the cashier is looking at a different bill from the one the
    server is about to issue."""
    with pytest.raises(TotalMismatch) as caught:
        assert_client_total_matches(D("2500.00"), D("2400.00"))
    assert caught.value.details == {"server_total": "2500.00", "client_total": "2400.00"}


# =============================================================================
# 5. Display (addendum §3.5)
# =============================================================================


@pytest.mark.parametrize(
    "stored,printed",
    [("240.00", "240"), ("3850.00", "3850"), ("0.00", "0"), ("1.00", "1"), ("100000.00", "100000")],
)
def test_amounts_print_with_no_decimal_places(stored: str, printed: str) -> None:
    """OD-23 / FR-PRINT-017: `240`, `3850`. No separators, no currency symbol."""
    assert format_for_display(D(stored)) == printed


def test_printed_amounts_have_no_thousands_separator() -> None:
    assert format_for_display(D("1234567.00")) == "1234567"
    assert "," not in format_for_display(D("1234567.00"))


def test_formatting_never_changes_the_stored_value() -> None:
    """T3-08: a property test asserting the stored decimal is unchanged by formatting,
    and that parsing the printed string back yields exactly the backend decimal."""
    for raw in ("0.00", "1.00", "240.00", "3850.00", "99999.00"):
        stored = D(raw)
        printed = format_for_display(stored)
        assert D(printed) == stored
        assert stored == D(raw)  # the original object is untouched


def test_a_fractional_amount_reaching_the_renderer_is_a_blocking_error() -> None:
    """Addendum §3.5 and the recorded OD-23 conflict: do **not** silently round.

    Handing out a bill that understates or overstates the stored amount is worse than
    refusing to print one, because nobody downstream can tell it happened.
    """
    with pytest.raises(ValidationFailed) as caught:
        format_for_display(D("240.50"))
    message = caught.value.details["fields"]["amount"]
    assert "must not be printed" in message


def test_display_decimal_places_are_policy_driven() -> None:
    two_places = MoneyPolicy(version=98, display_decimal_places=2)
    assert format_for_display(D("240.50"), two_places) == "240.50"


@pytest.mark.parametrize(
    "stored,printed",
    [("2.000", "2"), ("1.500", "1.5"), ("0.250", "0.25"), ("10.000", "10"), ("100.000", "100")],
)
def test_quantities_print_in_their_shortest_exact_form(stored: str, printed: str) -> None:
    assert format_quantity(D(stored)) == printed


# =============================================================================
# 6. Amount in words
# =============================================================================


@pytest.mark.parametrize(
    "value,words",
    [
        ("0", "Zero Rupees Only"),
        ("1", "One Rupees Only"),
        ("99", "Ninety Nine Rupees Only"),
        ("100", "One Hundred Rupees Only"),
        ("100000", "One Lakh Rupees Only"),
        ("10000000", "One Crore Rupees Only"),
        ("2500", "Two Thousand Five Hundred Rupees Only"),
        ("123456", "One Lakh Twenty Three Thousand Four Hundred Fifty Six Rupees Only"),
    ],
)
def test_amount_in_words_uses_the_indian_numbering_system(value: str, words: str) -> None:
    assert amount_in_words(D(value)) == words


def test_amount_in_words_handles_paise() -> None:
    assert amount_in_words(D("240.50")) == "Two Hundred Forty Rupees and Fifty Paise Only"


# =============================================================================
# 7. Quantity validation (E-18)
# =============================================================================

SET_UNIT = UnitSpec(code="SET", allows_fraction=False, decimal_places=0)
LITRE_UNIT = UnitSpec(code="LTR", allows_fraction=True, decimal_places=3)


def test_whole_quantity_on_an_integer_unit_is_accepted() -> None:
    assert validate_quantity(D("2.000"), SET_UNIT) == D("2.000")


def test_fractional_quantity_on_an_integer_unit_is_rejected() -> None:
    """E-18. A set of brake shoes cannot be sold in halves."""
    with pytest.raises(ValidationFailed) as caught:
        validate_quantity(D("0.500"), SET_UNIT)
    assert "whole units only" in caught.value.details["fields"]["quantity"]


def test_fractional_quantity_on_a_fractional_unit_is_accepted() -> None:
    assert validate_quantity(D("0.500"), LITRE_UNIT) == D("0.500")


def test_quantity_beyond_the_unit_s_precision_is_rejected() -> None:
    coarse = UnitSpec(code="KG", allows_fraction=True, decimal_places=1)
    with pytest.raises(ValidationFailed):
        validate_quantity(D("1.250"), coarse)


@pytest.mark.parametrize("value", ["0", "0.000", "-1", "-0.500"])
def test_non_positive_quantities_are_rejected(value: str) -> None:
    with pytest.raises(ValidationFailed):
        validate_quantity(D(value), LITRE_UNIT)


def test_unknown_unit_defaults_to_the_strict_option() -> None:
    """Refusing a fractional quantity is recoverable; silently accepting one the shop
    cannot dispense is not."""
    from bm.domain.shared.quantity import STRICT_UNIT

    assert STRICT_UNIT.allows_fraction is False


# =============================================================================
# 8. Policy object published to clients
# =============================================================================


def test_policy_payload_carries_everything_a_client_needs() -> None:
    """The client must never hardcode a rule that bootstrap provides
    (`../bmfrontend/CLAUDE.md §5` rule 15)."""
    payload = DEFAULT_POLICY.as_payload()
    assert payload["version"] == 1
    assert payload["currency"] == "INR"
    assert payload["rounding_mode"] == "reject_fractional"
    assert payload["round_grand_total_to_rupee"] is False
    assert payload["require_integral_amounts"] is True
    assert payload["display_decimal_places"] == 0


def test_the_v1_default_is_the_documented_policy() -> None:
    """A silent change to any of these is a change to the owner's bill."""
    assert DEFAULT_POLICY.version == 1
    assert DEFAULT_POLICY.rounding_mode is RoundingMode.REJECT_FRACTIONAL
    assert DEFAULT_POLICY.round_grand_total_to_rupee is False
    assert DEFAULT_POLICY.require_integral_amounts is True
    assert DEFAULT_POLICY.display_decimal_places == 0
    assert DEFAULT_POLICY.amount_scale == 2
    assert DEFAULT_POLICY.unit_price_scale == 4
    assert DEFAULT_POLICY.quantity_scale == 3


def test_is_integral_helper() -> None:
    assert is_integral(D("5")) is True
    assert is_integral(D("5.00")) is True
    assert is_integral(D("5.01")) is False
