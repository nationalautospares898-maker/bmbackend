"""Turning an uploaded file into candidate rows (FR-IMP-001, FR-IMP-006, FR-IMP-010).

The parser's only job is to say *what the file appears to contain*. It never decides
what to do about it: no product is created, no price is changed, no stock moves. That
separation is what makes the review step meaningful — a parse is a reading of a
supplier's spreadsheet, and a supplier's spreadsheet is not an authority on this shop's
catalogue.

Three deliberate choices:

* **A row that cannot be understood is kept, not dropped.** `ParsedRow.error` records
  why, the raw values are preserved verbatim, and the row surfaces in review as
  `invalid`. Silently skipping a malformed line is how half a price list gets imported
  without anybody noticing the other half.
* **Money is parsed from text with the same strictness as the API.** A cell containing
  `1,250.50` is read as `1250.50`; one containing `~1250` is an error, not a guess.
* **PDF extraction is explicitly low-confidence** (FR-IMP-010). Every row from a PDF is
  flagged so the matcher can force a human decision on all of them, however clean the
  text looked.
"""

from __future__ import annotations

import contextlib
import csv
import io
import re
import zlib
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any

#: Header text → the field it feeds, after `_normalize_header`.
#:
#: Suppliers do not agree on column names and never will; this is the observed
#: vocabulary from the shop's own files plus the obvious synonyms. An unrecognised
#: header is not an error — it is preserved in `raw_values` and ignored for matching,
#: because a supplier's spreadsheet usually carries columns that are none of our
#: business.
HEADER_SYNONYMS: dict[str, str] = {
    # name
    "name": "name",
    "item": "name",
    "itemname": "name",
    "description": "name",
    "particulars": "name",
    "product": "name",
    "productname": "name",
    # sku / part code
    "sku": "sku",
    "code": "sku",
    "partno": "sku",
    "partnumber": "sku",
    "partcode": "sku",
    "itemcode": "sku",
    "productcode": "sku",
    "catalogno": "sku",
    "oemno": "sku",
    # quantity
    "qty": "quantity",
    "quantity": "quantity",
    "nos": "quantity",
    "pcs": "quantity",
    "units": "quantity",
    # cost
    "cost": "unit_cost",
    "unitcost": "unit_cost",
    "rate": "unit_cost",
    "purchaserate": "unit_cost",
    "purchaseprice": "unit_cost",
    "basicrate": "unit_cost",
    "netrate": "unit_cost",
    # selling price
    "mrp": "selling_price",
    "price": "selling_price",
    "sellingprice": "selling_price",
    "saleprice": "selling_price",
    "saleRate": "selling_price",
    "salerate": "selling_price",
    "retail": "selling_price",
    # barcode
    "barcode": "barcode",
    "ean": "barcode",
    "upc": "barcode",
    # free text
    "brand": "brand",
    "make": "brand",
    "company": "brand",
    "unit": "unit",
    "uom": "unit",
    "hsn": "hsn_code",
    "hsncode": "hsn_code",
}

#: The fields a row can carry. Anything else in the file stays in `raw_values` only.
MAPPED_FIELDS: frozenset[str] = frozenset(HEADER_SYNONYMS.values())

#: How many leading rows to search for a header before giving up.
#:
#: Supplier spreadsheets routinely open with a letterhead, an address and a blank line.
#: Eight is enough for every file in the shop's folder and small enough that a file with
#: no header at all fails quickly rather than scanning 10 000 rows looking for one.
HEADER_SEARCH_DEPTH = 8

#: A parsed quantity/price must fit the column it lands in (`DATA-MODEL.md §1`).
_MAX_QUANTITY = Decimal("99999999999.999")
_MAX_PRICE = Decimal("9999999999.9999")


class ParseFailed(Exception):
    """The file as a whole could not be read — not a single bad row.

    Distinct from a row-level error: this ends the job in `failed`, whereas a bad row
    ends up in the review queue.
    """


@dataclass(frozen=True, slots=True)
class ParsedRow:
    """One candidate line. `error` set means the row is `invalid` (FR-IMP-006)."""

    row_index: int
    raw_values: dict[str, Any]
    name: str | None = None
    sku: str | None = None
    barcode: str | None = None
    brand: str | None = None
    unit: str | None = None
    hsn_code: str | None = None
    quantity: Decimal | None = None
    unit_cost: Decimal | None = None
    selling_price: Decimal | None = None
    error: str | None = None


@dataclass(frozen=True, slots=True)
class ParseResult:
    rows: list[ParsedRow] = field(default_factory=list)
    #: Header text as found, for the review screen to show which column became which
    #: field. A mapping the user cannot see is a mapping the user cannot correct.
    column_map: dict[str, str] = field(default_factory=dict)
    #: True for PDF sources: every row needs review regardless of how it parsed
    #: (FR-IMP-010).
    low_confidence: bool = False
    #: Non-fatal notes about the file, e.g. an unreadable sheet or a dropped blank tail.
    notes: list[str] = field(default_factory=list)


def _normalize_header(value: object) -> str:
    """Lower-case, alphanumerics only — `Part No.`, `part_no` and `PARTNO` are one key."""
    return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())


def _clean_text(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_number(value: object, *, maximum: Decimal) -> Decimal | None:
    """Read a spreadsheet cell as a decimal, or raise `ValueError` with a reason.

    Accepts what suppliers actually type — thousands separators, a trailing `/-`, a
    currency symbol, parentheses for negatives — and refuses anything else rather than
    guessing. `None` for an empty cell, which is a missing value, not a zero: a price
    list with a blank price column must not set every price to nothing.
    """
    if value is None:
        return None
    if isinstance(value, bool):
        raise ValueError("a yes/no value is not a number")
    if isinstance(value, int):
        parsed = Decimal(value)
    elif isinstance(value, Decimal):
        parsed = value
    elif isinstance(value, float):
        # openpyxl hands back floats for numeric cells. The cell's own displayed value
        # is the authority, so render at a fixed scale rather than letting the binary
        # expansion through: 0.1 must become "0.1", not 0.1000000000000000055511151231.
        parsed = Decimal(f"{value:.4f}")
    else:
        text = str(value).strip()
        if not text:
            return None
        negative = text.startswith("(") and text.endswith(")")
        text = text.strip("()")
        text = re.sub(r"[₹$€£]", "", text)
        text = re.sub(r"/-\s*$", "", text)
        text = text.replace(",", "").replace(" ", "")
        if not text:
            return None
        if not re.fullmatch(r"-?\d*\.?\d*", text) or text in {"", ".", "-", "-."}:
            raise ValueError(f"{str(value).strip()!r} is not a number")
        try:
            parsed = Decimal(text)
        except InvalidOperation as exc:
            raise ValueError(f"{str(value).strip()!r} is not a number") from exc
        if negative:
            parsed = -parsed

    if not parsed.is_finite():
        raise ValueError("not a finite number")
    if abs(parsed) > maximum:
        raise ValueError("too large for this field")
    return parsed


def _row_from_cells(row_index: int, cells: dict[str, Any], column_map: dict[str, str]) -> ParsedRow:
    """Build one `ParsedRow`, collecting the *first* problem as its error.

    First rather than all: the review screen shows one reason per row, and a cashier
    fixing a row fixes the thing they are told about, then sees the next.
    """
    mapped: dict[str, Any] = {}
    for header, raw in cells.items():
        target = column_map.get(_normalize_header(header))
        if target and mapped.get(target) in (None, ""):
            mapped[target] = raw

    error: str | None = None
    numbers: dict[str, Decimal | None] = {}
    for field_name, maximum in (
        ("quantity", _MAX_QUANTITY),
        ("unit_cost", _MAX_PRICE),
        ("selling_price", _MAX_PRICE),
    ):
        try:
            numbers[field_name] = _parse_number(mapped.get(field_name), maximum=maximum)
        except ValueError as exc:
            numbers[field_name] = None
            error = error or f"{field_name.replace('_', ' ')}: {exc}"

    for field_name in ("quantity", "unit_cost", "selling_price"):
        number = numbers[field_name]
        if number is not None and number < 0:
            error = error or f"{field_name.replace('_', ' ')} cannot be negative"
    if numbers["quantity"] is not None and numbers["quantity"] == 0:
        error = error or "quantity is zero"

    name = _clean_text(mapped.get("name"))
    sku = _clean_text(mapped.get("sku"))
    if not name and not sku:
        error = error or "no product name or part code in this row"

    return ParsedRow(
        row_index=row_index,
        raw_values={str(k): (None if v is None else str(v)) for k, v in cells.items()},
        name=name,
        sku=sku,
        barcode=_clean_text(mapped.get("barcode")),
        brand=_clean_text(mapped.get("brand")),
        unit=_clean_text(mapped.get("unit")),
        hsn_code=_clean_text(mapped.get("hsn_code")),
        quantity=numbers["quantity"],
        unit_cost=numbers["unit_cost"],
        selling_price=numbers["selling_price"],
        error=error,
    )


def _find_header(rows: list[list[Any]]) -> tuple[int, list[str], dict[str, str]]:
    """Locate the header row and the column map it implies.

    The header is the first row within `HEADER_SEARCH_DEPTH` that maps **at least two**
    known columns — one is too easy to hit by accident on a letterhead line containing
    the word "Rate".
    """
    best: tuple[int, list[str], dict[str, str]] | None = None
    for index, row in enumerate(rows[:HEADER_SEARCH_DEPTH]):
        headers = [str(cell).strip() if cell is not None else "" for cell in row]
        mapping = {
            _normalize_header(h): HEADER_SYNONYMS[_normalize_header(h)]
            for h in headers
            if _normalize_header(h) in HEADER_SYNONYMS
        }
        if len(set(mapping.values())) >= 2 and (best is None or len(mapping) > len(best[2])):
            best = (index, headers, mapping)
    if best is None:
        raise ParseFailed(
            "No recognisable column headings were found in the first "
            f"{HEADER_SEARCH_DEPTH} rows. The file needs a row naming at least two of "
            "the columns, for example a part code column and a rate column."
        )
    return best


def _rows_to_result(grid: list[list[Any]], *, notes: list[str]) -> ParseResult:
    header_index, headers, column_map = _find_header(grid)
    parsed: list[ParsedRow] = []
    for offset, raw_row in enumerate(grid[header_index + 1 :], start=1):
        if all(cell is None or str(cell).strip() == "" for cell in raw_row):
            continue
        cells = {
            headers[i] if i < len(headers) and headers[i] else f"column_{i + 1}": value
            for i, value in enumerate(raw_row)
        }
        parsed.append(_row_from_cells(offset, cells, column_map))
    if not parsed:
        raise ParseFailed("The file has column headings but no data rows below them.")
    return ParseResult(rows=parsed, column_map=dict(column_map), notes=notes)


def parse_csv(content: bytes) -> ParseResult:
    notes: list[str] = []
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("latin-1")
        notes.append("The file was not valid UTF-8; it was read as Latin-1.")

    sample = text[:4096]
    try:
        dialect: Any = csv.Sniffer().sniff(sample, delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel  # a single-column file sniffs as nothing; comma is correct
    grid = [list(row) for row in csv.reader(io.StringIO(text), dialect)]
    if not grid:
        raise ParseFailed("The file is empty.")
    return _rows_to_result(grid, notes=notes)


def parse_xlsx(content: bytes) -> ParseResult:
    # Imported here rather than at module scope: openpyxl pulls in a non-trivial amount
    # of machinery, and the billing path — which must start fast — never imports it.
    from openpyxl import load_workbook

    notes: list[str] = []
    try:
        # read_only for memory, data_only so a formula cell yields its cached *value*.
        # A price list whose rates are formulas is common, and importing "=B2*1.18" as a
        # price would be absurd.
        workbook = load_workbook(io.BytesIO(content), read_only=True, data_only=True)
    except Exception as exc:
        raise ParseFailed(f"The spreadsheet could not be opened: {exc}") from exc

    try:
        if not workbook.sheetnames:
            raise ParseFailed("The spreadsheet has no sheets.")
        # The first sheet only. Importing every sheet of a supplier's workbook would
        # silently pull in their terms-and-conditions tab; selecting a sheet is a
        # feature to add when a real file needs it, not a default to guess at.
        sheet = workbook[workbook.sheetnames[0]]
        if len(workbook.sheetnames) > 1:
            notes.append(
                f"Only the first sheet ({workbook.sheetnames[0]!r}) was read; "
                f"{len(workbook.sheetnames) - 1} other sheet(s) were ignored."
            )
        grid = [list(row) for row in sheet.iter_rows(values_only=True)]
    finally:
        workbook.close()

    if not grid:
        raise ParseFailed("The first sheet is empty.")
    return _rows_to_result(grid, notes=notes)


# --- PDF (FR-IMP-010, best-effort, P2) ---------------------------------------

#: One token of interest in a content stream, in document order.
#:
#: Either a string literal — `(literal)`, with `\)` escapes allowed — or one of the
#: operators that ends a line of text. Scanning for *both* in one pass is what preserves
#: the row structure: a PDF carries no newlines, so the only evidence that two strings
#: belong to different rows is a positioning operator between them.
#:
#: `Td`/`TD` move to a new line origin, `T*` advances one line, `'` and `"` show a
#: string *after* advancing, and `ET` ends the text object. Any of them closes the row
#: being accumulated.
_PDF_TOKEN = re.compile(rb"\((?:\\.|[^\\()])*\)|\bT[dD*]\b|\bET\b|(?<![A-Za-z])['\"](?![A-Za-z])")
_PDF_ROW_SPLIT = re.compile(r"\s{2,}|\t|\s*\|\s*")


def _pdf_text_lines(content: bytes) -> list[str]:
    """Pull visible text out of a PDF's content streams, one row per text line.

    Deliberately minimal: find the content streams, inflate the compressed ones, and
    read the string literals handed to the text operators — breaking a line wherever a
    positioning operator says the text moved. This handles the machine-generated price
    lists suppliers actually email and makes no attempt at scanned images, unusual
    encodings, or recovering columns from x-coordinates.

    Getting the *line breaks* right matters more than it looks: without them every
    string in the document concatenates into one line, the header and the first data row
    merge, and the file parses as "headings with no data below them". A PDF has no
    newline character, so these operators are the only signal there is.

    That the columns are then recovered from runs of whitespace — rather than from the
    text matrix — is exactly why FR-IMP-010 classes this as best-effort and why every
    PDF row is forced through human review.
    """
    lines: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", content, re.DOTALL):
        blob = match.group(1)
        # Uncompressed streams are legal and common in simple generators, so a failure
        # to inflate means "already plain", not "broken file".
        with contextlib.suppress(zlib.error):
            blob = zlib.decompress(blob)

        current: list[str] = []

        def flush(accumulated: list[str] = current) -> None:
            text = " ".join(accumulated).strip()
            if text:
                lines.append(text)
            accumulated.clear()

        for token in _PDF_TOKEN.finditer(blob):
            raw = token.group(0)
            if not raw.startswith(b"("):
                flush()
                continue
            piece = raw[1:-1].decode("latin-1")
            piece = re.sub(r"\\([()\\])", r"\1", piece)
            current.append(piece)
        flush()
    return lines


def parse_pdf(content: bytes) -> ParseResult:
    """Best-effort PDF extraction. Always `low_confidence=True` (FR-IMP-010)."""
    lines = _pdf_text_lines(content)
    if not lines:
        raise ParseFailed(
            "No text could be read from this PDF. If it is a scan, the rows have to be "
            "entered by hand or re-exported as a spreadsheet."
        )

    grid = [[cell.strip() for cell in _PDF_ROW_SPLIT.split(line) if cell.strip()] for line in lines]
    grid = [row for row in grid if row]
    try:
        result = _rows_to_result(
            grid,
            notes=[
                "Text was read from a PDF, which is approximate. Every row needs "
                "checking against the supplier's own document."
            ],
        )
    except ParseFailed as exc:
        raise ParseFailed(f"{exc} (read from a PDF, where column layout is approximate)") from exc

    return ParseResult(
        rows=result.rows,
        column_map=result.column_map,
        low_confidence=True,
        notes=result.notes,
    )


#: extension → parser.
PARSERS = {".csv": parse_csv, ".xlsx": parse_xlsx, ".pdf": parse_pdf}


def parse(extension: str, content: bytes) -> ParseResult:
    parser = PARSERS.get(extension)
    if parser is None:  # pragma: no cover - upload validation rejects this first
        raise ParseFailed(f"No parser for {extension!r}.")
    return parser(content)
