"""M4 — money conservation, as a property rather than a list of examples.

The reconciliation identity `total = subtotal - discount_total + tax_total +
rounding_adjustment` is enforced by a database `CHECK` (`ck_invoices__total`), so a
violation is not a wrong number on a screen — it is a failed insert at a busy counter.
Example-based tests cover the values someone thought of; Hypothesis covers the ones
nobody did.

The generators deliberately produce awkward values: prices with paise, quantities with
three decimal places, percentages with three, and combinations whose exact product has
more precision than any stored column.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from bm.domain.shared.errors import ValidationFailed
from bm.domain.shared.money import (
    DEFAULT_POLICY,
    InvoiceDiscount,
    LineInput,
    MoneyPolicy,
    RoundingMode,
    compute_invoice_totals,
    compute_line,
    format_for_display,
    is_integral,
)

D = Decimal

#: Prices at the column's full `NUMERIC(14,4)` precision, including sub-rupee values.
prices = st.decimals(
    min_value=D("0.0001"),
    max_value=D("99999.9999"),
    places=4,
    allow_nan=False,
    allow_infinity=False,
)
#: Whole-rupee prices, which the v1 policy actually permits.
whole_prices = st.integers(min_value=1, max_value=99_999).map(D)

quantities = st.decimals(
    min_value=D("0.001"), max_value=D("9999.999"), places=3, allow_nan=False, allow_infinity=False
)
whole_quantities = st.integers(min_value=1, max_value=999).map(D)

percents = st.decimals(
    min_value=D("0"), max_value=D("100"), places=3, allow_nan=False, allow_infinity=False
)

#: Every policy combination, so the algorithm is proven parameterised rather than
#: tuned to one rule (M4's stated purpose).
policies = st.sampled_from(
    [
        MoneyPolicy(version=1),
        MoneyPolicy(
            version=2, rounding_mode=RoundingMode.LINE_HALF_UP, require_integral_amounts=False
        ),
        MoneyPolicy(
            version=4,
            rounding_mode=RoundingMode.LINE_HALF_UP,
            round_grand_total_to_rupee=True,
            require_integral_amounts=False,
        ),
    ]
)


@st.composite
def line_inputs(draw: st.DrawFn, *, whole_only: bool = False) -> LineInput:
    return LineInput(
        quantity=draw(whole_quantities if whole_only else quantities),
        unit_price=draw(whole_prices if whole_only else prices),
        discount_percent=draw(percents) if draw(st.booleans()) else D("0"),
        discount_amount=D("0"),
        tax_rate_percent=D("0"),  # tax stays estimate_only until FR-TAX-003 passes
    )


@settings(max_examples=400, deadline=None)
@given(lines=st.lists(line_inputs(), min_size=1, max_size=25), policy=policies)
def test_totals_always_reconcile_or_are_refused(
    lines: list[LineInput], policy: MoneyPolicy
) -> None:
    """The central property: an invoice either reconciles exactly, or is refused.

    There is no third outcome. A committed total that does not satisfy
    `ck_invoices__total` must be impossible, and a refusal under the v1 policy is the
    documented behaviour for a fractional result, not a failure.
    """
    try:
        totals = compute_invoice_totals(lines, policy=policy)
    except ValidationFailed:
        return
    assert totals.reconciles(), (
        f"subtotal={totals.subtotal} discount={totals.discount_total} "
        f"tax={totals.tax_total} rounding={totals.rounding_adjustment} "
        f"total={totals.total}"
    )


@settings(max_examples=300, deadline=None)
@given(lines=st.lists(line_inputs(), min_size=1, max_size=25), policy=policies)
def test_subtotal_is_always_the_exact_sum_of_line_extensions(
    lines: list[LineInput], policy: MoneyPolicy
) -> None:
    """NFR-DATA-002b. The invoice must never disagree with the rows that explain it."""
    try:
        totals = compute_invoice_totals(lines, policy=policy)
    except ValidationFailed:
        return
    assert totals.subtotal == sum(line.taxable_amount for line in totals.lines)


@settings(max_examples=300, deadline=None)
@given(lines=st.lists(line_inputs(whole_only=True), min_size=1, max_size=25))
def test_whole_rupee_inputs_are_never_refused_by_the_v1_policy(
    lines: list[LineInput],
) -> None:
    """The v1 policy must not be *over*-strict.

    Whole-rupee prices with whole quantities and no discount always extend to a whole
    rupee, so a refusal here would mean the shop could not bill an ordinary sale.
    """
    plain = [
        LineInput(
            quantity=line.quantity,
            unit_price=line.unit_price,
            discount_percent=D("0"),
            discount_amount=D("0"),
        )
        for line in lines
    ]
    totals = compute_invoice_totals(plain, policy=DEFAULT_POLICY)
    assert is_integral(totals.total)
    assert totals.reconciles()


@settings(max_examples=300, deadline=None)
@given(lines=st.lists(line_inputs(whole_only=True), min_size=1, max_size=25))
def test_every_v1_total_round_trips_through_the_printed_string(
    lines: list[LineInput],
) -> None:
    """T3-08: parsing the printed string back must yield exactly the backend decimal.

    This is the property that makes the zero-decimal bill safe: the figure on paper is
    the figure in the database, not a rounded representation of it.
    """
    plain = [LineInput(quantity=line.quantity, unit_price=line.unit_price) for line in lines]
    totals = compute_invoice_totals(plain, policy=DEFAULT_POLICY)
    assert D(format_for_display(totals.total)) == totals.total
    for line in totals.lines:
        assert D(format_for_display(line.line_total)) == line.line_total


@settings(max_examples=300, deadline=None)
@given(quantity=whole_quantities, price=whole_prices)
def test_line_extension_is_commutative_with_scaling(quantity: Decimal, price: Decimal) -> None:
    """`n` units at `p` must equal one unit at `n × p`.

    If these ever disagree, the engine has an ordering or precision bug that no single
    example would expose.
    """
    many = compute_line(LineInput(quantity=quantity, unit_price=price))
    one = compute_line(LineInput(quantity=D(1), unit_price=quantity * price))
    assert many.taxable_amount == one.taxable_amount


@settings(max_examples=300, deadline=None)
@given(
    quantity=whole_quantities,
    price=whole_prices,
    percent=st.sampled_from([D("0"), D("5"), D("10"), D("25"), D("50"), D("100")]),
)
def test_discount_never_produces_a_negative_or_inflated_line(
    quantity: Decimal, price: Decimal, percent: Decimal
) -> None:
    """A discount can take a line to zero but never below, and never above its gross."""
    try:
        totals = compute_line(
            LineInput(quantity=quantity, unit_price=price, discount_percent=percent)
        )
    except ValidationFailed:
        return
    assert totals.taxable_amount >= 0
    assert totals.taxable_amount <= totals.gross


@settings(max_examples=200, deadline=None)
@given(
    lines=st.lists(line_inputs(whole_only=True), min_size=1, max_size=10),
    discount=st.integers(min_value=0, max_value=100).map(D),
)
def test_invoice_discount_reconciles_for_every_amount_it_permits(
    lines: list[LineInput], discount: Decimal
) -> None:
    plain = [LineInput(quantity=line.quantity, unit_price=line.unit_price) for line in lines]
    try:
        totals = compute_invoice_totals(
            plain, invoice_discount=InvoiceDiscount(amount=discount), policy=DEFAULT_POLICY
        )
    except ValidationFailed:
        return
    assert totals.reconciles()
    assert totals.total == totals.subtotal - discount


@pytest.mark.parametrize(
    "quantity,price",
    [("3.000", "333.3333"), ("7.000", "142.8571"), ("0.001", "0.0001"), ("0.333", "3.0001")],
)
def test_known_awkward_products_are_refused_rather_than_rounded(quantity: str, price: str) -> None:
    """Regression guards for the values a rounding bug would hide behind."""
    with pytest.raises(ValidationFailed):
        compute_line(LineInput(quantity=D(quantity), unit_price=D(price)))
