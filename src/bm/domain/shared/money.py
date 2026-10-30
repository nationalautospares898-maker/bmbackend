"""The money engine.

This is the only place monetary arithmetic happens (`ARCHITECTURE.md §4.1`). Every rule
below comes from §3 of the [Design Completion Addendum]
(../../../docs/PHASE-2-DESIGN-COMPLETION-ADDENDUM-2026-10-30.md), which separates five
concerns that earlier records mixed together: **input validation**, **calculation
precision**, **stored values**, **rounding**, and **display**.

The v1 policy, in one paragraph
-------------------------------
Exact decimal arithmetic throughout, never binary floating point. Every finalised
monetary amount must be an exact whole number of rupees; a fractional price, discount,
line result or payment is **rejected before finalisation** with a typed validation
error. **No rounding is performed at all** — the rounding adjustment is exactly zero —
so the owner's zero-decimal bill shows the true figure rather than a rounded one
presented as exact. Quantities keep their unit's configured precision, so a fractional
quantity is fine as long as its extension lands on a whole rupee.

Why reject rather than round
----------------------------
The owner requires `PRICE` and `GRAND TOTAL` to print with no decimal places (OD-23).
Three options existed: print a rounded figure, print a truncated figure, or refuse to
finalise a bill whose true value cannot be shown exactly. The first two hand the
customer a document that disagrees with the stored record by up to 99 paise and give
nobody a way to notice. The third is inconvenient exactly once — when someone types a
price with paise — and is honest every other time. R-37 is mitigated by this choice plus
render-blocking validation.

The algorithm is **policy-parameterised**, not hardcoded to these values (M4's whole
purpose). `RoundingMode.LINE_HALF_UP` and `round_grand_total_to_rupee` are implemented
and tested so that if the owner's accountant later authorises paise or whole-rupee
rounding, the change is a policy version and a settings row — not a rewrite. Switching
is a **recorded decision with updated tests**, never an implicit default change.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation, localcontext
from enum import StrEnum

from bm.domain.shared.errors import ValidationFailed

#: Currency is fixed for v1. It is still recorded on every invoice snapshot so a future
#: multi-currency change cannot silently reinterpret historical rows.
CURRENCY = "INR"

ZERO = Decimal("0")
ONE_RUPEE = Decimal("1")

#: Working precision for intermediate products. 28 significant digits is far beyond
#: `NUMERIC(14,4) × NUMERIC(14,3)`, so no intermediate result is ever truncated.
_WORKING_PRECISION = 40


class RoundingMode(StrEnum):
    """How a fractional monetary result is treated.

    `REJECT_FRACTIONAL` is the v1 default. `LINE_HALF_UP` is the earlier Phase 1
    proposal, implemented and tested so the engine stays a policy-parameterised
    algorithm rather than one hardcoded rule (M4's stated purpose).

    **Why there is no `INVOICE_HALF_UP` mode.** An "keep line totals exact, round only
    the invoice" policy was implemented first and then removed, because a property test
    proved it cannot satisfy the data model. Line amounts are stored in
    `NUMERIC(14,2)` columns, so a line extension such as `0.001 × 1 = 0.0000001` is not
    representable; rounding it for storage then breaks NFR-DATA-002b
    (`invoices.subtotal = Σ invoice_items.line_total`), while *not* rounding it breaks
    the column. Either way the invoice stops agreeing with the rows that explain it.
    Any mode that rounds must therefore round **at the line**, which is exactly what
    `LINE_HALF_UP` does; whole-rupee rounding of the grand total then layers on top via
    `round_grand_total_to_rupee`, recorded in its own column. Together those cover the
    precision/method/stage/whole-rupee space OD-7 would choose from.
    """

    #: v1: a fractional result is a validation error. No value is ever altered.
    REJECT_FRACTIONAL = "reject_fractional"
    #: Round each line to the amount scale, then sum (the earlier Phase 1 proposal).
    LINE_HALF_UP = "line_half_up"


@dataclass(frozen=True, slots=True)
class MoneyPolicy:
    """The money policy object, published to clients by `GET /sync/bootstrap`.

    `version` is persisted on every invoice (`invoices.money_policy_version`, T3-03) so
    a disputed historical bill can be explained under the rule that produced it. A
    policy change never alters a stored invoice.
    """

    #: Incremented whenever any field below changes. Never reused.
    version: int = 1
    currency: str = CURRENCY

    #: Stored scales (`DATA-MODEL.md §1`). Amounts are `NUMERIC(14,2)` for schema
    #: consistency and future compatibility even though v1 constrains them to integers.
    amount_scale: int = 2
    unit_price_scale: int = 4
    quantity_scale: int = 3
    percent_scale: int = 3

    rounding_mode: RoundingMode = RoundingMode.REJECT_FRACTIONAL
    #: Never enabled implicitly. Requires a written replacement policy first.
    round_grand_total_to_rupee: bool = False

    #: v1: every finalised monetary amount is an exact whole number of rupees.
    require_integral_amounts: bool = True

    #: Decimal places shown on the printed bill. `0` matches the workbook's number
    #: format (OD-23). Display never changes a stored value.
    display_decimal_places: int = 0

    def as_payload(self) -> dict[str, object]:
        """The shape the client receives. Clients must never hardcode these
        (`../bmfrontend/CLAUDE.md §5` rule 15)."""
        return {
            "version": self.version,
            "currency": self.currency,
            "amount_scale": self.amount_scale,
            "unit_price_scale": self.unit_price_scale,
            "quantity_scale": self.quantity_scale,
            "percent_scale": self.percent_scale,
            "rounding_mode": str(self.rounding_mode),
            "round_grand_total_to_rupee": self.round_grand_total_to_rupee,
            "require_integral_amounts": self.require_integral_amounts,
            "display_decimal_places": self.display_decimal_places,
        }


#: The v1 default. A change requires a recorded decision and updated tests before the
#: M4 exit gate; it is **not** an owner or accountant approval of accounting policy
#: (addendum §2, OD-7/OD-23).
DEFAULT_POLICY = MoneyPolicy()


# --- parsing ------------------------------------------------------------------


def parse_decimal(raw: object, *, field_name: str) -> Decimal:
    """Parse a decimal **string** strictly.

    Rejected, rather than coerced:

    * `float` — it has already lost precision before arriving. Accepting one would mean
      accepting a value nobody can audit.
    * exponent notation — `1e3` is numerically 1000 but its precision is ambiguous.
    * `NaN` / `Infinity` — not monetary values.

    `int` is accepted because a whole number cannot lose precision in JSON.
    """
    if isinstance(raw, Decimal):
        value = raw
    elif isinstance(raw, bool):
        raise ValidationFailed({field_name: "Must be a decimal string."})
    elif isinstance(raw, int):
        value = Decimal(raw)
    elif isinstance(raw, float):
        raise ValidationFailed(
            {field_name: "Must be a decimal string, not a number — numbers lose precision."}
        )
    elif isinstance(raw, str):
        text = raw.strip()
        if not text:
            raise ValidationFailed({field_name: "Must be a decimal string."})
        lowered = text.lower()
        if "e" in lowered or "inf" in lowered or "nan" in lowered:
            raise ValidationFailed(
                {field_name: "Exponent notation and non-finite values are not accepted."}
            )
        try:
            value = Decimal(text)
        except InvalidOperation as exc:
            raise ValidationFailed({field_name: "Must be a decimal string."}) from exc
    else:
        raise ValidationFailed({field_name: "Must be a decimal string."})

    if not value.is_finite():
        raise ValidationFailed({field_name: "Must be a finite decimal."})
    return value


def assert_scale_within(value: Decimal, *, max_places: int, field_name: str) -> Decimal:
    """Reject excess precision instead of silently rounding it away.

    A value with too many decimal places is a `422`, never a quiet truncation
    (`SECURITY.md §4`): the caller meant something, and guessing which part to discard
    is how a discount disappears.
    """
    exponent = value.as_tuple().exponent
    places = -int(exponent) if isinstance(exponent, int) and exponent < 0 else 0
    if places > max_places:
        raise ValidationFailed(
            {
                field_name: (
                    f"At most {max_places} decimal place{'s' if max_places != 1 else ''} "
                    f"allowed; received {places}."
                )
            }
        )
    return value


def assert_not_negative(value: Decimal, *, field_name: str) -> Decimal:
    if value < ZERO:
        raise ValidationFailed({field_name: "Must not be negative."})
    return value


def is_integral(value: Decimal) -> bool:
    """True when the value is an exact whole number of rupees."""
    return value == value.to_integral_value()


def assert_integral_amount(value: Decimal, *, field_name: str, policy: MoneyPolicy) -> Decimal:
    """Enforce the whole-rupee rule.

    Under the v1 policy a fractional monetary value is refused **before** finalisation,
    which is the only way the zero-decimal bill can be both faithful to the owner's
    format and honest about the amount.
    """
    if not policy.require_integral_amounts:
        return value
    if not is_integral(value):
        raise ValidationFailed(
            {
                field_name: (
                    "Amounts must be whole rupees on this bill format. "
                    f"{format(value, 'f')} has paise — adjust the figure, "
                    "it will not be rounded for you."
                )
            }
        )
    return value


def quantize_amount(value: Decimal, policy: MoneyPolicy) -> Decimal:
    """Set a value to the stored amount scale **without changing it**.

    `quantize` with `ROUND_HALF_UP` would alter a fractional value; this helper is only
    ever applied to values already proven integral (or already rounded by an explicitly
    enabled rounding mode), so it is a representation change, not an arithmetic one.
    """
    return value.quantize(Decimal(1).scaleb(-policy.amount_scale))


# --- line arithmetic ----------------------------------------------------------


@dataclass(frozen=True, slots=True)
class LineInput:
    """One sale line, already parsed and scale-validated."""

    quantity: Decimal
    unit_price: Decimal
    discount_percent: Decimal = ZERO
    discount_amount: Decimal = ZERO
    tax_rate_percent: Decimal = ZERO
    line_no: int = 1


@dataclass(frozen=True, slots=True)
class LineTotals:
    gross: Decimal
    discount_total: Decimal
    taxable_amount: Decimal
    tax_amount: Decimal
    line_total: Decimal


def compute_line(line: LineInput, policy: MoneyPolicy = DEFAULT_POLICY) -> LineTotals:
    """Extend one line in exact decimal arithmetic.

    Order is fixed and matters: `quantity × unit_price`, then the percentage discount,
    then the monetary discount, then tax on the net. Applying the monetary discount
    before the percentage one would make the percentage mean something different, and
    two code paths disagreeing about that order is a class of bug that only shows up in
    reconciliation months later.
    """
    with localcontext() as context:
        context.prec = _WORKING_PRECISION

        gross = line.quantity * line.unit_price

        percent_discount = gross * line.discount_percent / Decimal(100)
        discount_total = percent_discount + line.discount_amount
        if discount_total > gross:
            raise ValidationFailed(
                {f"lines.{line.line_no}.discount": ("Discount is larger than the line value.")}
            )

        net = gross - discount_total

        # The whole-rupee rule applies to the **post-discount, pre-tax extension**
        # (addendum §3.1). A fractional quantity is acceptable; a fractional rupee
        # result is not.
        if policy.rounding_mode is RoundingMode.REJECT_FRACTIONAL:
            assert_integral_amount(
                net, field_name=f"lines.{line.line_no}.line_total", policy=policy
            )
        elif policy.rounding_mode is RoundingMode.LINE_HALF_UP:
            net = net.quantize(Decimal(1).scaleb(-policy.amount_scale), rounding=ROUND_HALF_UP)
            discount_total = gross - net

        tax_amount = net * line.tax_rate_percent / Decimal(100)
        if policy.rounding_mode is RoundingMode.LINE_HALF_UP:
            tax_amount = tax_amount.quantize(
                Decimal(1).scaleb(-policy.amount_scale), rounding=ROUND_HALF_UP
            )
        else:
            # Under REJECT_FRACTIONAL, tax is only reachable once GST is enabled
            # (FR-TAX-003). The whole-rupee rule applies to it as well, so a rate that
            # produces paise is refused rather than rounded.
            if tax_amount and not is_integral(tax_amount):
                assert_integral_amount(
                    tax_amount, field_name=f"lines.{line.line_no}.tax_amount", policy=policy
                )

        line_total = net + tax_amount

        # Every returned amount is exactly representable at the stored scale. This is
        # the invariant that keeps `Σ line_total == subtotal` true after persistence —
        # see the RoundingMode docstring for why a mode without it was removed.
        totals = LineTotals(
            gross=quantize_amount(gross, policy),
            discount_total=quantize_amount(discount_total, policy),
            taxable_amount=quantize_amount(net, policy),
            tax_amount=quantize_amount(tax_amount, policy),
            line_total=quantize_amount(line_total, policy),
        )

    _assert_storable(totals, policy, line_no=line.line_no)
    return totals


def _assert_storable(totals: LineTotals, policy: MoneyPolicy, *, line_no: int) -> None:
    """Fail loudly if an amount would be silently altered by the database.

    `quantize_amount` uses the default rounding, so a value with more precision than
    `amount_scale` would be quietly changed on the way into `NUMERIC(14,2)`. Under
    every supported policy that cannot happen; this asserts it rather than assuming it,
    because the failure mode is a stored figure that disagrees with the arithmetic that
    produced it.
    """
    step = Decimal(1).scaleb(-policy.amount_scale)
    for name, value in (
        ("gross", totals.gross),
        ("discount_total", totals.discount_total),
        ("taxable_amount", totals.taxable_amount),
        ("tax_amount", totals.tax_amount),
        ("line_total", totals.line_total),
    ):
        if value != value.quantize(step):
            raise ValidationFailed(
                {
                    f"lines.{line_no}.{name}": (
                        f"{format(value, 'f')} has more precision than this bill can "
                        "store. Adjust the figure — it will not be rounded for you."
                    )
                }
            )


# --- invoice arithmetic -------------------------------------------------------


@dataclass(frozen=True, slots=True)
class InvoiceDiscount:
    percent: Decimal = ZERO
    amount: Decimal = ZERO


@dataclass(frozen=True, slots=True)
class InvoiceTotals:
    """Satisfies `ck_invoices__total`:
    `total = subtotal - discount_total + tax_total + rounding_adjustment`.

    The database `CHECK` makes NFR-DATA-002 a guarantee rather than a hope, so these
    five fields must reconcile exactly or the insert fails — which is the intended
    behaviour, not an inconvenience.

    ⚠️ **Definition of `subtotal`, and a documented specification ambiguity.**
    Here `subtotal` is the sum of **post-discount, pre-tax** line extensions
    (`Σ invoice_items.taxable_amount`). That is the only reading under which
    `ck_invoices__total` is arithmetically sound in general: if `subtotal` were
    `Σ invoice_items.line_total` and `line_total` included tax, then adding `tax_total`
    again would double-count it.

    `DATA-MODEL.md` NFR-DATA-002b states `invoices.subtotal = Σ invoice_items.line_total`.
    Both readings coincide exactly in v1, because `tax.mode = estimate_only` makes every
    `tax_amount` zero, so `line_total == taxable_amount` on every row. The two would
    diverge only if GST were enabled, which is separately gated behind FR-TAX-003.
    Recorded rather than silently resolved: enabling GST requires restating NFR-DATA-002b
    in terms of `taxable_amount`, and that is a documentation change the tax milestone
    must make deliberately.
    """

    subtotal: Decimal
    #: Invoice-level discount only. Line-level discounts are already reflected in each
    #: line's net extension and therefore in `subtotal`; counting them here as well
    #: would break `ck_invoices__total`.
    discount_total: Decimal
    tax_total: Decimal
    rounding_adjustment: Decimal
    total: Decimal
    lines: tuple[LineTotals, ...] = field(default=())
    #: Reported for the audit trail, not part of the reconciliation identity.
    line_discount_total: Decimal = ZERO

    def reconciles(self) -> bool:
        return (
            self.total
            == self.subtotal - self.discount_total + self.tax_total + self.rounding_adjustment
        )


def compute_invoice_totals(
    lines: list[LineInput],
    *,
    invoice_discount: InvoiceDiscount | None = None,
    policy: MoneyPolicy = DEFAULT_POLICY,
) -> InvoiceTotals:
    """Compose the invoice from its lines.

    `subtotal` is the exact sum of accepted line extensions (NFR-DATA-002b), so the
    invoice total is never an independently computed figure that could drift from the
    rows that explain it.
    """
    if not lines:
        from bm.domain.shared.errors import EmptySale

        raise EmptySale()

    discount = invoice_discount or InvoiceDiscount()

    with localcontext() as context:
        context.prec = _WORKING_PRECISION

        computed = tuple(compute_line(line, policy) for line in lines)

        subtotal = sum((line.taxable_amount for line in computed), start=ZERO)
        line_discounts = sum((line.discount_total for line in computed), start=ZERO)
        tax_total = sum((line.tax_amount for line in computed), start=ZERO)

        invoice_percent_discount = subtotal * discount.percent / Decimal(100)
        invoice_level_discount = invoice_percent_discount + discount.amount
        if invoice_level_discount > subtotal:
            raise ValidationFailed({"invoice_discount": "Discount is larger than the bill value."})

        if policy.rounding_mode is RoundingMode.REJECT_FRACTIONAL and invoice_level_discount:
            assert_integral_amount(
                invoice_level_discount, field_name="invoice_discount", policy=policy
            )

        net = subtotal - invoice_level_discount + tax_total

        rounding_adjustment = ZERO
        if policy.round_grand_total_to_rupee:
            # Explicitly enabled only. The adjustment is recorded as its own column so
            # the arithmetic still reconciles and the rounding is visible rather than
            # concealed inside the total (addendum §3.4).
            rounded = net.quantize(ONE_RUPEE, rounding=ROUND_HALF_UP)
            rounding_adjustment = rounded - net
            net = rounded
        elif policy.rounding_mode is RoundingMode.REJECT_FRACTIONAL:
            assert_integral_amount(net, field_name="total", policy=policy)

        totals = InvoiceTotals(
            subtotal=quantize_amount(subtotal, policy),
            discount_total=quantize_amount(invoice_level_discount, policy),
            tax_total=quantize_amount(tax_total, policy),
            rounding_adjustment=quantize_amount(rounding_adjustment, policy),
            total=quantize_amount(net, policy),
            lines=computed,
            line_discount_total=quantize_amount(line_discounts, policy),
        )

    if not totals.reconciles():
        # Unreachable by construction; asserted because the database CHECK would
        # otherwise reject the insert with a far less useful message.
        raise ValidationFailed(
            {
                "total": (
                    "Internal arithmetic did not reconcile. Nothing was saved. Please report this."
                )
            }
        )
    return totals


# --- cross-check --------------------------------------------------------------


def assert_client_total_matches(server_total: Decimal, client_total: Decimal | None) -> None:
    """`client_computed_total` is a cross-check, never a value to trust (FR-SALE-003).

    A mismatch is `422 TOTAL_MISMATCH` and is never silently overridden: it means the
    cashier is looking at a different bill from the one the server is about to issue.
    """
    if client_total is None:
        return
    if server_total != client_total:
        from bm.domain.shared.errors import TotalMismatch

        raise TotalMismatch(
            details={
                "server_total": format(server_total, "f"),
                "client_total": format(client_total, "f"),
            }
        )


# --- display ------------------------------------------------------------------


def format_for_display(value: Decimal, policy: MoneyPolicy = DEFAULT_POLICY) -> str:
    """Render an amount for the printed bill.

    OD-23 / FR-PRINT-017: ungrouped, no decimal places, no separators, no currency
    symbol — `240`, `3850`. Matching the workbook's `0` number format.

    A non-integral value reaching here is a **blocking error**, not something to round
    (addendum §3.5). Handing out a bill that understates or overstates the stored amount
    is worse than refusing to print one, because nobody downstream can tell it happened.
    """
    if policy.display_decimal_places == 0:
        if not is_integral(value):
            raise ValidationFailed(
                {
                    "amount": (
                        f"{format(value, 'f')} cannot be shown on a whole-rupee bill. "
                        "This bill must not be printed until the amount is corrected."
                    )
                }
            )
        return str(int(value))
    return format(value.quantize(Decimal(1).scaleb(-policy.display_decimal_places)), "f")


def format_quantity(value: Decimal) -> str:
    """Trim a quantity to its shortest exact form: `2.000` prints as `2`, `1.500`
    as `1.5`. Never rounded — only trailing zeros are removed."""
    normalised = value.normalize()
    exponent = normalised.as_tuple().exponent
    if isinstance(exponent, int) and exponent > 0:
        # `normalize()` turns 100 into 1E+2; expand it back to plain digits.
        normalised = normalised.quantize(ONE_RUPEE)
    return format(normalised, "f")


# --- amount in words ----------------------------------------------------------

_UNITS = (
    "Zero",
    "One",
    "Two",
    "Three",
    "Four",
    "Five",
    "Six",
    "Seven",
    "Eight",
    "Nine",
    "Ten",
    "Eleven",
    "Twelve",
    "Thirteen",
    "Fourteen",
    "Fifteen",
    "Sixteen",
    "Seventeen",
    "Eighteen",
    "Nineteen",
)
_TENS = (
    "",
    "",
    "Twenty",
    "Thirty",
    "Forty",
    "Fifty",
    "Sixty",
    "Seventy",
    "Eighty",
    "Ninety",
)


def _below_hundred(value: int) -> str:
    if value < 20:
        return _UNITS[value]
    tens, units = divmod(value, 10)
    return _TENS[tens] + (f" {_UNITS[units]}" if units else "")


def _below_thousand(value: int) -> str:
    hundreds, remainder = divmod(value, 100)
    parts = []
    if hundreds:
        parts.append(f"{_UNITS[hundreds]} Hundred")
    if remainder:
        parts.append(_below_hundred(remainder))
    return " ".join(parts)


def amount_in_words(value: Decimal) -> str:
    """Indian numbering system: thousand, lakh, crore.

    ⚠️ **The printed bill must not render this.** The owner's workbook was inspected
    part by part on 2026-10-30 and an amount-in-words line is **verified absent** from
    the whole sheet (OD-5); adding one would be inventing a field. The helper exists
    because `print_payload` carries the value in the API contract and a future
    owner-approved template may use it — a payload field is not a licence to render.
    """
    if value < ZERO:
        return f"Minus {amount_in_words(-value)}"

    rupees = int(value.to_integral_value(rounding="ROUND_DOWN"))
    paise = int(((value - rupees) * 100).to_integral_value(rounding=ROUND_HALF_UP))

    if rupees == 0 and paise == 0:
        return "Zero Rupees Only"

    groups: list[str] = []
    crore, remainder = divmod(rupees, 10_000_000)
    lakh, remainder = divmod(remainder, 100_000)
    thousand, below_thousand = divmod(remainder, 1_000)

    if crore:
        groups.append(f"{amount_in_words(Decimal(crore)).replace(' Rupees Only', '')} Crore")
    if lakh:
        groups.append(f"{_below_thousand(lakh)} Lakh")
    if thousand:
        groups.append(f"{_below_thousand(thousand)} Thousand")
    if below_thousand:
        groups.append(_below_thousand(below_thousand))

    words = " ".join(part for part in groups if part).strip()
    if paise:
        return f"{words} Rupees and {_below_hundred(paise)} Paise Only"
    return f"{words} Rupees Only"
