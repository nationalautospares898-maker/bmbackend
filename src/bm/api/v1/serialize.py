"""Serialisers shared between routers.

`invoice_dict` lives here, not in `invoices.py`, because two routes return an invoice:
`POST /sales:finalize` and `GET /invoices/{id}`. They had drifted into two shapes for
the same resource — the finalisation body described the customer as `name`/`phone` while
the detail body used `name_snapshot`/`phone_snapshot`, and only the detail carried
`returned_quantity`.

That is a client-breaking difference: the confirmation card and the Daily Bills row
describe the same bill, and a cashier checking one against the other must not see
different fields. `tests/api/test_response_contract.py` caught it; one serialiser is
what stops it recurring.

The API layer may not import an ORM model (`ARCHITECTURE.md §3` rule 4), so everything
here takes the dataclasses from `bm.domain.sales.reader`.
"""

from __future__ import annotations

from typing import Any

from bm.domain.sales.reader import InvoiceRecord


def invoice_dict(record: InvoiceRecord, *, may_see_cost: bool) -> dict[str, Any]:
    """The `invoice` resource, in the one shape every route returns it in.

    `may_see_cost` **omits** `unit_cost_snapshot` rather than nulling it: a nulled field
    invites a client to render a placeholder where a margin should be, and FR-CAT-004 is
    about the value never leaking through a careless render.
    """
    return {
        "id": str(record.id),
        # Internal reference, available on a detail view and labelled internal (T3-07).
        # It is never rendered to a customer and never in `print_payload` (OD-3).
        "internal_reference": record.internal_reference,
        "sequence_number": record.sequence_number,
        "document_kind": record.document_kind,
        "status": record.status,
        "issued_at": record.issued_at.isoformat(),
        "is_credit": record.is_credit,
        "due_date": record.due_date.isoformat() if record.due_date else None,
        "customer": {
            "id": str(record.customer_id) if record.customer_id else None,
            # `*_snapshot` because these are the values as they were when the bill was
            # issued. A customer who later changes their phone number does not change a
            # bill that has already been printed.
            "name_snapshot": record.customer_name_snapshot,
            "business_name_snapshot": record.customer_business_name_snapshot,
            "vehicle_number_snapshot": record.vehicle_number_snapshot,
            "mechanic_name_snapshot": record.mechanic_name_snapshot,
            "phone_snapshot": record.phone_snapshot,
        },
        "items": [
            {
                # The line and the product it sold. Needed by a correction: `POST
                # /returns` identifies a line by `invoice_item_id`, and rebuilding a
                # cancelled bill needs the `product_id` to add the line again. Without
                # these two fields no correction workflow can address a line at all,
                # which is why none existed.
                "id": str(item.id),
                "product_id": str(item.product_id),
                "line_no": item.line_no,
                "product_name_snapshot": item.product_name_snapshot,
                "sku_snapshot": item.sku_snapshot,
                "unit_code_snapshot": item.unit_code_snapshot,
                "quantity": format(item.quantity, "f"),
                "unit_price": format(item.unit_price, "f"),
                "discount_amount": format(item.discount_amount, "f"),
                "tax_rate_snapshot": format(item.tax_rate_snapshot, "f"),
                "tax_amount": format(item.tax_amount, "f"),
                "line_total": format(item.line_total, "f"),
                # Zero on a new bill. Present regardless, so a client rendering a
                # returnable quantity does not have to special-case a fresh invoice.
                "returned_quantity": format(item.returned_quantity, "f"),
                **(
                    {
                        "unit_cost_snapshot": (
                            format(item.unit_cost_snapshot, "f")
                            if item.unit_cost_snapshot is not None
                            else None
                        )
                    }
                    if may_see_cost
                    else {}
                ),
            }
            for item in record.items
        ],
        "subtotal": format(record.subtotal, "f"),
        "discount_total": format(record.discount_total, "f"),
        "tax_total": format(record.tax_total, "f"),
        "rounding_adjustment": format(record.rounding_adjustment, "f"),
        "total": format(record.total, "f"),
        "amount_paid": format(record.amount_paid, "f"),
        "outstanding": format(record.outstanding, "f"),
        # Derived from the allocations, never settable (ADR-022).
        "payment_status": record.payment_status,
        "oversell_override": record.oversell_override,
        "oversell_reason": record.oversell_reason,
        # T3-03: the money rule that produced this bill.
        "money_policy_version": record.money_policy_version,
        "cancelled_at": record.cancelled_at.isoformat() if record.cancelled_at else None,
        "cancellation_reason": record.cancellation_reason,
    }
