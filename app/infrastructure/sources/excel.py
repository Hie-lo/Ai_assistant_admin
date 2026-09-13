"""Excel/XLSX source adapter (openpyxl).

Rules (spec sections 6, 15):
- First non-empty row is the header row.
- Duplicate/empty headers are detected and reported (disambiguation is the
  mapping layer's job).
- A read that raises mid-sheet is INCOMPLETE: callers must not run
  missing-row inference on it.
- Cell values are stringified (numbers keep a plain representation; None ->
  "").
"""

from __future__ import annotations

from app.infrastructure.sources import SourceRead


def _cell_to_str(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).strip()


def read_xlsx(file_bytes: bytes, *, sheet_name: str | None = None) -> SourceRead:
    """Parse an XLSX file into (headers, rows) with completeness reporting."""
    import io

    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(file_bytes), read_only=True, data_only=True)
    except Exception as exc:  # noqa: BLE001 - report as unreadable source
        return SourceRead(complete=False, error=f"unreadable workbook: {type(exc).__name__}")

    try:
        if sheet_name:
            if sheet_name not in wb.sheetnames:
                return SourceRead(
                    complete=False,
                    error=f"sheet '{sheet_name}' not found (available: {', '.join(wb.sheetnames)})",
                )
            ws = wb[sheet_name]
        else:
            ws = wb.active
        if ws is None:
            return SourceRead(complete=False, error="workbook has no usable sheet")

        grid: list[list[object]] = []
        try:
            for row in ws.iter_rows(values_only=True):
                grid.append(list(row))
        except Exception as exc:  # noqa: BLE001 - partial read
            return SourceRead(complete=False, error=f"partial read: {type(exc).__name__}")

        # Locate the header row: first row with at least two non-empty cells.
        header_idx = next(
            (i for i, row in enumerate(grid) if sum(1 for c in row if _cell_to_str(c)) >= 2),
            None,
        )
        if header_idx is None:
            return SourceRead(complete=False, error="no header row found")

        raw_headers = [_cell_to_str(c) for c in grid[header_idx]]
        # Drop fully empty trailing columns.
        while raw_headers and not raw_headers[-1]:
            raw_headers.pop()
        if not raw_headers:
            return SourceRead(complete=False, error="empty header row")

        rows: list[dict[str, str]] = []
        for row in grid[header_idx + 1 :]:
            values = [_cell_to_str(c) for c in row]
            # Skip fully empty rows.
            if not any(values):
                continue
            # Pad/truncate to header width.
            values = (values + [""] * len(raw_headers))[: len(raw_headers)]
            rows.append(dict(zip(raw_headers, values, strict=True)))
        return SourceRead(rows=rows, headers=raw_headers, complete=True, row_count=len(rows))
    finally:
        wb.close()
