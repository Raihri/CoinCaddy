"""Inspect and parse an uploaded CSV/XLSX file into header names and neutral cells.

The file type is decided from content, and the extension must agree. Whole-file rejections
(ImportRejected) cover anything unsafe or ambiguous: wrong type, binary or non-UTF-8 text, encrypted
or legacy Excel, macros, formulas, zip bombs, several data sheets, bad headers, and size/row/column
limits. Row-level problems are left to the normalizer so they show up as rejected rows.

Cells are JSON-ready: None, {"t": "text", "v": str}, {"t": "num", "v": decimal str}, {"t": "date", "v": iso}.
"""

import csv
import io
import zipfile
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal

PARSER_VERSION = "import-v1"

MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ROWS = 5000
MAX_COLUMNS = 30
MAX_CELL_CHARS = 500
MAX_ZIP_ENTRIES = 200
MAX_UNCOMPRESSED_BYTES = 25 * 1024 * 1024
MAX_COMPRESSION_RATIO = 200

OLE_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"  # legacy .xls and password-encrypted .xlsx
ZIP_MAGIC = b"PK\x03\x04"


class ImportRejected(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class ParsedFile:
    file_type: str
    headers: list[str]
    rows: list[list[dict | None]]  # data rows only; row_number = index + 2


def parse_upload(filename: str, data: bytes) -> ParsedFile:
    if not data:
        raise ImportRejected("empty_file", "The file is empty.")
    if len(data) > MAX_FILE_BYTES:
        raise ImportRejected("too_large", f"The file is larger than {MAX_FILE_BYTES // (1024 * 1024)} MB.")
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ("csv", "xlsx"):
        raise ImportRejected("unsupported_type", "Only .csv and .xlsx files are supported.")
    if data.startswith(OLE_MAGIC):
        raise ImportRejected("encrypted_or_legacy", "Encrypted workbooks and legacy .xls files are not accepted. Save as an unprotected .xlsx or .csv.")
    is_zip = data.startswith(ZIP_MAGIC)
    if ext == "xlsx":
        if not is_zip:
            raise ImportRejected("type_mismatch", "The file is named .xlsx but its content is not an Excel workbook.")
        return _parse_xlsx(data)
    if is_zip:
        raise ImportRejected("type_mismatch", "The file is named .csv but its content is a zip/Excel file.")
    return _parse_csv(data)


# --- CSV -------------------------------------------------------------------------------------------


def _parse_csv(data: bytes) -> ParsedFile:
    if b"\x00" in data:
        raise ImportRejected("binary_content", "The file contains binary data, not CSV text.")
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise ImportRejected("encoding", "The CSV must be UTF-8 encoded.") from None
    delimiter = _detect_delimiter(text)
    csv.field_size_limit(MAX_CELL_CHARS * 20)
    try:
        records = list(csv.reader(io.StringIO(text, newline=""), delimiter=delimiter, strict=True))
    except csv.Error as exc:
        raise ImportRejected("malformed_csv", f"The CSV is malformed ({exc}).") from None
    records = [r for r in records if any(c.strip() for c in r)]
    if not records:
        raise ImportRejected("no_data", "The file has no rows.")
    headers = _check_headers(records[0])
    body = records[1:]
    _check_row_count(len(body))
    return ParsedFile("csv", headers, [[_text_cell(c) for c in r] for r in body])


def _detect_delimiter(text: str) -> str:
    sample = [line for line in text.splitlines()[:50] if line.strip()]
    if not sample:
        raise ImportRejected("no_data", "The file has no rows.")
    fits = []
    for d in (",", ";", "\t"):
        try:
            rows = list(csv.reader(sample, delimiter=d))
        except csv.Error:
            continue
        width = len(rows[0])
        if width >= 2 and sum(len(r) == width for r in rows) >= 0.9 * len(rows):
            fits.append(d)
    if not fits:
        raise ImportRejected("no_columns", "Could not find at least two columns separated by comma, semicolon, or tab.")
    if len(fits) > 1:
        raise ImportRejected("ambiguous_delimiter", "The column separator is ambiguous (more than one of comma, semicolon, tab fits).")
    return fits[0]


def _text_cell(value: str) -> dict | None:
    return {"t": "text", "v": value} if value.strip() else None


# --- XLSX ------------------------------------------------------------------------------------------


def _check_zip(data: bytes) -> None:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        raise ImportRejected("corrupt_workbook", "The workbook is damaged or not a valid .xlsx file.") from None
    infos = zf.infolist()
    if len(infos) > MAX_ZIP_ENTRIES:
        raise ImportRejected("workbook_too_complex", "The workbook has too many internal parts.")
    total = 0
    for info in infos:
        total += info.file_size
        if info.compress_size and info.file_size / info.compress_size > MAX_COMPRESSION_RATIO:
            raise ImportRejected("suspicious_compression", "The workbook expands far beyond its size and was rejected.")
        if info.flag_bits & 0x1:
            raise ImportRejected("encrypted_or_legacy", "Encrypted workbooks are not accepted.")
    if total > MAX_UNCOMPRESSED_BYTES:
        raise ImportRejected("suspicious_compression", "The workbook expands far beyond its size and was rejected.")
    names = {i.filename for i in infos}
    if "[Content_Types].xml" not in names or "xl/workbook.xml" not in names:
        raise ImportRejected("type_mismatch", "The file is not an Excel .xlsx workbook.")
    content_types = zf.read("[Content_Types].xml").decode("utf-8", "replace")
    if any(n.lower().endswith("vbaproject.bin") for n in names) or "macroEnabled" in content_types:
        raise ImportRejected("macros", "Workbooks containing macros are not accepted.")


def _parse_xlsx(data: bytes) -> ParsedFile:
    _check_zip(data)
    import openpyxl  # imported lazily; defusedxml is installed so openpyxl parses XML safely

    try:
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=False)
    except Exception as exc:  # noqa: BLE001
        raise ImportRejected("corrupt_workbook", f"The workbook could not be read ({type(exc).__name__}).") from None
    try:
        sheets = []
        for ws in wb.worksheets:
            rows = _read_sheet(ws)
            if rows:
                sheets.append(rows)
    finally:
        wb.close()
    if not sheets:
        raise ImportRejected("no_data", "The workbook has no data.")
    if len(sheets) > 1:
        raise ImportRejected("ambiguous_sheets", "The workbook has data on more than one sheet. Keep one sheet per import.")
    rows = sheets[0]
    header_cells = rows[0]
    if any(c is not None and c["t"] != "text" for c in header_cells):
        raise ImportRejected("bad_headers", "The first row must contain text column names.")
    headers = _check_headers([c["v"] if c else "" for c in header_cells])
    body = rows[1:]
    _check_row_count(len(body))
    width = len(headers)
    return ParsedFile("xlsx", headers, [r[:width] + [None] * (width - len(r)) if len(r) < width else r for r in body])


def _read_sheet(ws) -> list[list[dict | None]]:
    out: list[list[dict | None]] = []
    for row in ws.iter_rows():
        cells: list[dict | None] = []
        for cell in row:
            if getattr(cell, "data_type", None) == "f":
                raise ImportRejected("formulas", f"Formulas are not accepted (cell {cell.coordinate}). Paste values only and upload again.")
            cells.append(_xlsx_cell(cell.value))
        while cells and cells[-1] is None:
            cells.pop()
        if len(cells) > MAX_COLUMNS:
            raise ImportRejected("too_many_columns", f"The file has more than {MAX_COLUMNS} columns.")
        if cells:
            out.append(cells)
            if len(out) > MAX_ROWS + 1:
                raise ImportRejected("too_many_rows", f"The file has more than {MAX_ROWS} data rows.")
    return out


def _xlsx_cell(value) -> dict | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return {"t": "text", "v": "TRUE" if value else "FALSE"}
    if isinstance(value, datetime):
        return {"t": "date", "v": value.date().isoformat()}
    if isinstance(value, date):
        return {"t": "date", "v": value.isoformat()}
    if isinstance(value, (int, float, Decimal)):
        return {"t": "num", "v": str(Decimal(repr(value)) if isinstance(value, float) else Decimal(value))}
    text = str(value)
    return {"t": "text", "v": text} if text.strip() else None


# --- shared ----------------------------------------------------------------------------------------


def _check_headers(raw: list[str]) -> list[str]:
    if len(raw) > MAX_COLUMNS:
        raise ImportRejected("too_many_columns", f"The file has more than {MAX_COLUMNS} columns.")
    headers = [" ".join(h.split()) for h in raw]
    while headers and not headers[-1]:
        headers.pop()
    if len(headers) < 2:
        raise ImportRejected("bad_headers", "The first row must name at least two columns.")
    if any(not h for h in headers):
        raise ImportRejected("bad_headers", "Every column in the first row needs a name.")
    if any(len(h) > 60 for h in headers):
        raise ImportRejected("bad_headers", "Column names must be at most 60 characters.")
    if any(h[0] in "=+-@" for h in headers):
        raise ImportRejected("formulas", "Column names must not look like formulas.")
    lowered = [h.casefold() for h in headers]
    if len(set(lowered)) != len(lowered):
        raise ImportRejected("bad_headers", "Column names must be unique.")
    return headers


def _check_row_count(n: int) -> None:
    if n == 0:
        raise ImportRejected("no_data", "The file has a header row but no data rows.")
    if n > MAX_ROWS:
        raise ImportRejected("too_many_rows", f"The file has more than {MAX_ROWS} data rows.")
