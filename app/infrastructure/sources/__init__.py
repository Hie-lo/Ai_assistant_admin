"""Source adapters (Excel upload / Google Sheets).

V1 sources read rows as header -> raw string values. Normalization, mapping
and validation happen in the application layer, never in the adapter.
Adapters report whether the read was COMPLETE — incomplete reads must never
trigger missing-row inference (spec section 13).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.infrastructure.db.models import Source


@dataclass(frozen=True)
class SourceRead:
    """Result of a structural source read."""

    rows: list[dict[str, str]] = field(default_factory=list)
    headers: list[str] = field(default_factory=list)
    complete: bool = True
    row_count: int = 0
    error: str | None = None


def read_source(source: Source, *, file_bytes: bytes | None = None) -> SourceRead:
    """Dispatch to the adapter for the source kind.

    EXCEL_UPLOAD requires ``file_bytes`` (the customer re-uploads each
    import in V1; no server-side file persistence).
    GOOGLE_SHEETS reads the configured sheet/range via the API client.
    """
    from app.domain import enums
    from app.infrastructure.sources import excel, google_sheets

    if source.kind == enums.SourceKind.EXCEL_UPLOAD.value:
        if file_bytes is None:
            return SourceRead(complete=False, error="Excel source requires the uploaded file")
        return excel.read_xlsx(file_bytes, sheet_name=source.sheet_name)
    if source.kind == enums.SourceKind.GOOGLE_SHEETS.value:
        return google_sheets.read_spreadsheet(
            source,
            spreadsheet_id=source.external_ref or "",
            sheet_name=source.sheet_name,
            range_spec=source.range_spec,
        )
    return SourceRead(complete=False, error=f"Unsupported source kind: {source.kind}")
