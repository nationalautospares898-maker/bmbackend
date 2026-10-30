"""The import parser (FR-IMP-001, FR-IMP-006, FR-IMP-010).

Pure unit tests — no database. The parser's contract is "say what the file appears to
contain, and keep what you could not understand", so these fixtures are deliberately
the messy things a supplier actually sends: a letterhead above the header, thousands
separators, a currency symbol, a formula result, two sheets, a blank tail.

The one rule every test here is really checking: **a row the parser cannot read is kept
with a reason, never dropped.** Half an imported price list with no indication that the
other half vanished is the worst possible outcome, and it is the easy one to write by
accident.
"""

from __future__ import annotations

import io
import zlib
from decimal import Decimal

import pytest

from bm.domain.imports.parser import (
    HEADER_SEARCH_DEPTH,
    ParseFailed,
    parse,
    parse_csv,
    parse_pdf,
    parse_xlsx,
)


def _csv(text: str) -> bytes:
    return text.encode("utf-8")


# --- header detection ---------------------------------------------------------


def test_a_header_below_a_letterhead_is_still_found() -> None:
    """Supplier files open with their own name and address, not with column names."""
    result = parse_csv(
        _csv(
            "SRI BALAJI AUTO PARTS\n"
            "GT Road, Ludhiana\n"
            "\n"
            "Part No,Description,Qty,Rate\n"
            "TT-407-BS,Tie Rod End,4,330.00\n"
        )
    )
    assert len(result.rows) == 1
    row = result.rows[0]
    assert row.sku == "TT-407-BS"
    assert row.name == "Tie Rod End"
    assert row.quantity == Decimal("4")
    assert row.unit_cost == Decimal("330.00")
    assert row.error is None


def test_one_recognised_column_is_not_enough_to_be_a_header() -> None:
    """A letterhead line containing the word "Rate" must not be mistaken for a header.

    Two mapped columns is the threshold, and this is the case that chose it.
    """
    with pytest.raises(ParseFailed, match="column headings"):
        parse_csv(_csv("Best Rate Guaranteed\nTT-407-BS,4\n"))


def test_a_file_with_no_header_at_all_fails_with_an_actionable_message() -> None:
    with pytest.raises(ParseFailed) as exc:
        parse_csv(_csv("a,b,c\n1,2,3\n"))
    assert "part code" in str(exc.value)
    assert str(HEADER_SEARCH_DEPTH) in str(exc.value)


def test_header_synonyms_are_case_and_punctuation_insensitive() -> None:
    result = parse_csv(_csv("PART NO.,Item Name,MRP\nHF-3509,Oil Filter,250\n"))
    assert result.rows[0].sku == "HF-3509"
    assert result.rows[0].name == "Oil Filter"
    assert result.rows[0].selling_price == Decimal("250")


def test_the_column_map_is_reported_so_a_reviewer_can_see_the_interpretation() -> None:
    """A mapping the user cannot see is a mapping the user cannot correct."""
    result = parse_csv(_csv("Part No,Description,Rate\nX-1,Thing,10\n"))
    assert set(result.column_map.values()) == {"sku", "name", "unit_cost"}


def test_unrecognised_columns_are_preserved_in_raw_values_not_discarded() -> None:
    result = parse_csv(_csv("Part No,Rate,Supplier Remarks\nX-1,10,back order\n"))
    assert result.rows[0].raw_values["Supplier Remarks"] == "back order"


# --- number parsing -----------------------------------------------------------


@pytest.mark.parametrize(
    ("cell", "expected"),
    [
        # Quoted, because a thousands separator in a comma-delimited file *is* quoted —
        # that is how Excel writes it. An unquoted `1,250.50` is genuinely two cells.
        ('"1,250.50"', Decimal("1250.50")),
        ("₹2400", Decimal("2400")),
        ("330.00/-", Decimal("330.00")),
        ("1 250", Decimal("1250")),
        ("(50)", Decimal("-50")),
        ("", None),
    ],
)
def test_money_shapes_suppliers_actually_type(cell: str, expected: Decimal | None) -> None:
    result = parse_csv(_csv(f"Part No,Rate,Qty\nX-1,{cell},1\n"))
    assert result.rows[0].unit_cost == expected


def test_a_cell_that_is_not_a_number_makes_the_row_invalid_with_a_reason() -> None:
    """Not a guess, and not a silent zero."""
    result = parse_csv(_csv("Part No,Rate,Qty\nX-1,about 300,1\n"))
    row = result.rows[0]
    assert row.unit_cost is None
    assert row.error is not None
    assert "about 300" in row.error


def test_a_negative_quantity_is_refused_rather_than_absorbed() -> None:
    result = parse_csv(_csv("Part No,Qty,Rate\nX-1,-3,10\n"))
    assert result.rows[0].error == "quantity cannot be negative"


def test_a_zero_quantity_is_refused() -> None:
    """A zero-quantity receipt line is a line someone meant to delete."""
    result = parse_csv(_csv("Part No,Qty,Rate\nX-1,0,10\n"))
    assert result.rows[0].error == "quantity is zero"


def test_a_blank_price_column_does_not_become_a_price_of_zero() -> None:
    """The bug this guards: a price list with an empty column repricing the catalogue
    to nothing."""
    result = parse_csv(_csv("Part No,Description,MRP\nX-1,Thing,\n"))
    assert result.rows[0].selling_price is None
    assert result.rows[0].error is None


def test_a_number_too_large_for_its_column_is_an_error_not_a_truncation() -> None:
    result = parse_csv(_csv("Part No,Rate,Qty\nX-1,99999999999999,1\n"))
    assert result.rows[0].error is not None
    assert "too large" in result.rows[0].error


def test_a_row_with_neither_name_nor_part_code_is_invalid() -> None:
    result = parse_csv(_csv("Part No,Description,Rate\n,,10\n"))
    assert result.rows[0].error == "no product name or part code in this row"


def test_blank_rows_are_skipped_entirely_rather_than_recorded_as_invalid() -> None:
    """A trailing blank line is formatting, not data — recording it as a bad row would
    put noise in the review queue."""
    result = parse_csv(_csv("Part No,Rate,Qty\nX-1,10,1\n,,\n\nX-2,20,2\n"))
    assert len(result.rows) == 2


def test_row_index_follows_the_file_so_the_review_screen_matches_what_the_user_sees() -> None:
    result = parse_csv(_csv("Part No,Rate,Qty\nX-1,10,1\nX-2,20,2\n"))
    assert [r.row_index for r in result.rows] == [1, 2]


# --- csv specifics ------------------------------------------------------------


def test_a_semicolon_delimited_export_is_read() -> None:
    """European Excel exports semicolons. Sniffing is why this works."""
    result = parse_csv(_csv("Part No;Rate;Qty\nX-1;10;1\n"))
    assert result.rows[0].sku == "X-1"


def test_a_utf8_bom_does_not_end_up_in_the_first_header() -> None:
    """Excel writes a BOM. Without `utf-8-sig` the first column becomes `﻿Part No`
    and stops mapping."""
    result = parse_csv("﻿Part No,Rate,Qty\nX-1,10,1\n".encode())
    assert result.rows[0].sku == "X-1"


def test_a_latin1_file_is_read_with_a_note_rather_than_rejected() -> None:
    result = parse_csv("Part No,Description,Rate\nX-1,Dürr seal,10\n".encode("latin-1"))
    assert result.rows[0].name == "Dürr seal"
    assert any("Latin-1" in note for note in result.notes)


def test_an_empty_file_fails_clearly() -> None:
    with pytest.raises(ParseFailed):
        parse_csv(b"")


def test_a_header_with_no_data_below_it_fails_rather_than_importing_nothing() -> None:
    """Silently succeeding with zero rows would look like a working import."""
    with pytest.raises(ParseFailed, match="no data rows"):
        parse_csv(_csv("Part No,Rate,Qty\n"))


# --- xlsx ---------------------------------------------------------------------


def _workbook(rows: list[list[object]], *, sheets: int = 1) -> bytes:
    from openpyxl import Workbook

    workbook = Workbook()
    sheet = workbook.active
    assert sheet is not None
    for row in rows:
        sheet.append(row)
    for extra in range(1, sheets):
        workbook.create_sheet(f"Terms {extra}")
    buffer = io.BytesIO()
    workbook.save(buffer)
    return buffer.getvalue()


def test_a_spreadsheet_is_read_with_its_numeric_cells_intact() -> None:
    content = _workbook(
        [
            ["Part No", "Description", "Qty", "Rate"],
            ["6204-2RS", "Bearing", 10, 92.5],
        ]
    )
    result = parse_xlsx(content)
    assert result.rows[0].quantity == Decimal("10.0000")
    assert result.rows[0].unit_cost == Decimal("92.5000")


def test_only_the_first_sheet_is_read_and_the_rest_are_reported() -> None:
    """Importing every sheet would quietly pull in the supplier's terms-and-conditions
    tab as product rows."""
    content = _workbook([["Part No", "Rate"], ["X-1", 10]], sheets=3)
    result = parse_xlsx(content)
    assert any("other sheet" in note for note in result.notes)


def test_a_file_that_is_not_a_spreadsheet_fails_as_one_bad_file() -> None:
    with pytest.raises(ParseFailed, match="could not be opened"):
        parse_xlsx(b"PK\x03\x04 not really a workbook")


# --- pdf (FR-IMP-010) ---------------------------------------------------------


def _pdf(lines: list[str], *, compress: bool = True) -> bytes:
    """A minimal PDF carrying one content stream of text-showing operators.

    Shaped the way a real generator emits a table: `Td` moves to each new line origin
    before the string is shown, and `ET` closes the text object. A PDF has no newline
    character, so those operators are the *only* record that these are separate rows —
    a fixture built without them would pass against an extractor that merges the whole
    document into one line, which is precisely the bug this shape caught.
    """
    body = b"BT /F1 10 Tf 72 720 Td\n"
    for line in lines:
        body += f"({line}) Tj 0 -14 Td\n".encode("latin-1")
    body += b"ET\n"
    blob = zlib.compress(body) if compress else body
    return b"%PDF-1.4\n1 0 obj\nstream\n" + blob + b"\nendstream\nendobj\n"


def test_text_is_extracted_from_a_compressed_pdf_stream() -> None:
    content = _pdf(["Part No    Description    Rate", "X-1    Thing    10"])
    result = parse_pdf(content)
    assert result.rows[0].sku == "X-1"


def test_an_uncompressed_pdf_stream_is_also_read() -> None:
    content = _pdf(["Part No    Rate    Qty", "X-1    10    1"], compress=False)
    assert parse_pdf(content).rows[0].sku == "X-1"


def test_every_pdf_result_is_flagged_low_confidence() -> None:
    """FR-IMP-010. This flag is what forces a decision on every row, however clean the
    extraction looked."""
    result = parse_pdf(_pdf(["Part No    Rate    Qty", "X-1    10    1"]))
    assert result.low_confidence is True
    assert any("approximate" in note for note in result.notes)


def test_a_pdf_with_no_extractable_text_says_so_instead_of_importing_nothing() -> None:
    """A scan. The message has to tell the user what to do about it."""
    with pytest.raises(ParseFailed) as exc:
        parse_pdf(b"%PDF-1.4\nno streams here\n")
    assert "scan" in str(exc.value)


# --- dispatch -----------------------------------------------------------------


def test_parse_dispatches_on_the_sniffed_extension() -> None:
    assert parse(".csv", _csv("Part No,Rate\nX-1,10\n")).rows[0].sku == "X-1"
