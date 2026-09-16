"""Google Sheets source adapter.

Contract notes (spec sections 4, 19):
- Reads only the configured sheet/range (batch values.get), never the whole
  spreadsheet by default.
- The API client is obtained through an injectable factory so tests can run
  against a controlled fake without network or credentials.
- Any transport/API failure yields an INCOMPLETE read (no missing-row
  inference downstream); secrets are never logged.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from app.infrastructure.sources import SourceRead

# Test/ops hook: returns an authenticated sheets service object.
ClientFactory = Callable[[str], Any]  # credentials_ref -> service

_client_factory: ClientFactory | None = None


def set_client_factory(factory: ClientFactory | None) -> None:
    """Inject the client factory (tests / deployment wiring)."""
    global _client_factory
    _client_factory = factory


def _default_client_factory(credentials_ref: str) -> Any:
    # V1: the credentials file path is stored under the credentials_ref key
    # in the operator's secret store (path on the worker host).
    import json
    import os

    from google.oauth2.service_account import Credentials
    from googleapiclient.discovery import build

    path = os.environ.get(f"GOOGLE_SERVICE_ACCOUNT_{credentials_ref.upper()}")
    if not path:
        raise RuntimeError(f"no service-account file configured for '{credentials_ref}'")
    with open(path, encoding="utf-8") as fh:
        info = json.load(fh)
    creds = Credentials.from_service_account_info(
        info, scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"]
    )
    return build("sheets", "v4", credentials=creds, cache_discovery=False)


def _first_row_with_values(values: list[list[str]], min_cells: int = 2) -> int | None:
    for i, row in enumerate(values):
        if sum(1 for c in row if (c or "").strip()) >= min_cells:
            return i
    return None


def read_spreadsheet(
    source: Any,
    *,
    spreadsheet_id: str,
    sheet_name: str | None,
    range_spec: str | None,
) -> SourceRead:
    """Read the configured range of a Google Sheet into (headers, rows)."""
    if not spreadsheet_id:
        return SourceRead(complete=False, error="spreadsheet id is required")
    if _client_factory is None:
        if not source.credentials_ref:
            return SourceRead(complete=False, error="no credentials configured for source")
        factory = _default_client_factory
    else:
        factory = _client_factory

    try:
        service = factory(source.credentials_ref or "")
    except Exception as exc:  # noqa: BLE001 - surface as unreadable source
        return SourceRead(complete=False, error=f"client init failed: {type(exc).__name__}")

    if range_spec:
        rng = range_spec
    elif sheet_name:
        rng = f"'{sheet_name}'"
    else:
        rng = None  # first sheet

    try:
        if rng:
            resp = service.values().get(spreadsheetId=spreadsheet_id, range=rng).execute()
        else:
            resp = service.values().get(
                spreadsheetId=spreadsheet_id, range="A1:ZZ"
            ).execute()
        values = [[(c or "") for c in row] for row in resp.get("values", [])]
    except Exception as exc:  # noqa: BLE001 - permission loss / API error
        return SourceRead(complete=False, error=f"api read failed: {type(exc).__name__}")

    header_idx = _first_row_with_values(values)
    if header_idx is None:
        return SourceRead(complete=False, error="no header row found")

    raw_headers = [c.strip() for c in values[header_idx]]
    while raw_headers and not raw_headers[-1]:
        raw_headers.pop()
    if not raw_headers:
        return SourceRead(complete=False, error="empty header row")

    rows: list[dict[str, str]] = []
    for row in values[header_idx + 1 :]:
        values_row = [c.strip() for c in row]
        if not any(values_row):
            continue
        values_row = (values_row + [""] * len(raw_headers))[: len(raw_headers)]
        rows.append(dict(zip(raw_headers, values_row, strict=True)))
    return SourceRead(rows=rows, headers=raw_headers, complete=True, row_count=len(rows))
