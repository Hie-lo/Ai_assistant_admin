"""Google Sheets source adapter.

Contract notes (spec sections 4, 19):
- Reads only the configured sheet/range (batch values.get), never the whole
  spreadsheet by default.
- The API client is obtained through an injectable factory so tests can run
  against a controlled fake without network or credentials.
- Any transport/API failure yields an INCOMPLETE read (no missing-row
  inference downstream); secrets are never logged.
- V1 now supports public sheets without credentials via CSV export fallback
  (for customer-friendly UX: paste link -> auto read if public).
"""

from __future__ import annotations

import csv
import io
import re
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


_SPREADSHEET_ID_RE = re.compile(r"/d/([a-zA-Z0-9-_]+)")


def extract_spreadsheet_id(external_ref: str) -> str:
    """Extract spreadsheet ID from full URL or return as-is if already ID.

    Handles:
    - https://docs.google.com/spreadsheets/d/1hHnhr.../edit?usp=sharing
    - https://docs.google.com/spreadsheets/d/1hHnhr.../edit#gid=0
    - 1hHnhr... (raw ID)
    """
    if not external_ref:
        return ""
    ref = external_ref.strip()
    m = _SPREADSHEET_ID_RE.search(ref)
    if m:
        return m.group(1)
    # If it looks like a URL but no /d/ pattern, try to take last part?
    # Otherwise return as-is (assume it's already an ID)
    # Remove query params if someone pasted ID with ?usp...
    if "?" in ref and "/" not in ref:
        ref = ref.split("?")[0]
    return ref.strip()


def _try_public_csv_fetch(spreadsheet_id: str, sheet_name: str | None) -> SourceRead | None:
    """Try to fetch a public Google Sheet via CSV export (no auth needed).

    This works if the sheet is shared as 'Anyone with the link can view'.
    Uses httpx (already in dependencies) with short timeout.
    """
    try:
        import httpx
    except ImportError:
        return None

    # Build URLs to try: first with sheet name via gviz, then export
    urls: list[str] = []
    if sheet_name:
        # gviz with sheet name
        safe_sheet = sheet_name.strip()
        # URL encode sheet name roughly (httpx will handle)
        urls.append(
            f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv&sheet={safe_sheet}"
        )
    # Export first sheet or named sheet via export? export uses gid, not name, so try generic export
    urls.append(f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/export?format=csv")
    # Also try gviz without sheet param (first sheet)
    urls.append(f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/gviz/tq?tqx=out:csv")

    for url in urls:
        try:
            with httpx.Client(timeout=10.0, follow_redirects=True) as client:
                resp = client.get(url)
                # Google returns 200 even for HTML login page if not public, so check content
                if resp.status_code != 200:
                    continue
                text = resp.text
                # If response is HTML (login page), skip
                if "<html" in text.lower()[:500] and "text/html" in resp.headers.get("content-type", "").lower():
                    # Could still be CSV with html? Check if it contains comma and not too much html
                    if text.strip().startswith("<!DOCTYPE") or "<title>" in text[:1000].lower():
                        continue
                # Try parse as CSV
                # Use csv module to handle Persian etc
                f = io.StringIO(text)
                reader = csv.reader(f)
                values = [[(c or "").strip() for c in row] for row in reader]
                # Filter empty
                if not values:
                    continue
                # Find header row
                header_idx = _first_row_with_values(values, min_cells=1)
                if header_idx is None:
                    continue
                raw_headers = [c.strip() for c in values[header_idx]]
                # Remove trailing empty headers
                while raw_headers and not raw_headers[-1]:
                    raw_headers.pop()
                if not raw_headers:
                    continue
                # Must have at least 1 non-empty header that looks like product field, not just HTML
                if len(raw_headers) == 1 and len(raw_headers[0]) > 200:
                    continue
                rows: list[dict[str, str]] = []
                for row in values[header_idx + 1 :]:
                    values_row = [c.strip() for c in row]
                    if not any(values_row):
                        continue
                    # Pad/truncate to header length
                    values_row = (values_row + [""] * len(raw_headers))[: len(raw_headers)]
                    rows.append(dict(zip(raw_headers, values_row, strict=True)))
                if not rows and len(raw_headers) < 1:
                    continue
                return SourceRead(
                    rows=rows, headers=raw_headers, complete=True, row_count=len(rows)
                )
        except Exception:
            # Try next URL
            continue
    return None


def read_spreadsheet(
    source: Any,
    *,
    spreadsheet_id: str,
    sheet_name: str | None,
    range_spec: str | None,
) -> SourceRead:
    """Read the configured range of a Google Sheet into (headers, rows).

    Flow:
    1. If test client factory is injected, use it (for unit tests)
    2. Else if credentials_ref is set, try authenticated API
    3. Else try public CSV export (customer-friendly: paste link -> works if public)
    4. If all fail, return INCOMPLETE with helpful message (no secret leak)
    """
    if not spreadsheet_id:
        return SourceRead(complete=False, error="spreadsheet id is required")

    # Normalize spreadsheet_id: extract from full URL if needed
    normalized_id = extract_spreadsheet_id(spreadsheet_id)

    # 1. Test factory (unit tests) - if set, always use it regardless of credentials
    if _client_factory is not None:
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
            rng = None

        try:
            if rng:
                resp = service.values().get(spreadsheetId=normalized_id, range=rng).execute()
            else:
                resp = service.values().get(
                    spreadsheetId=normalized_id, range="A1:ZZ"
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

    # 2. Authenticated path if credentials_ref is set
    if source.credentials_ref:
        try:
            service = _default_client_factory(source.credentials_ref)
        except Exception as exc:
            return SourceRead(
                complete=False,
                error=f"client init failed: {type(exc).__name__} — بررسی کنید GOOGLE_SERVICE_ACCOUNT_{source.credentials_ref.upper()} ست شده باشد",
            )

        if range_spec:
            rng = range_spec
        elif sheet_name:
            rng = f"'{sheet_name}'"
        else:
            rng = None

        try:
            if rng:
                resp = service.values().get(spreadsheetId=normalized_id, range=rng).execute()
            else:
                resp = service.values().get(
                    spreadsheetId=normalized_id, range="A1:ZZ"
                ).execute()
            values = [[(c or "") for c in row] for row in resp.get("values", [])]
        except Exception as exc:
            return SourceRead(complete=False, error=f"api read failed: {type(exc).__name__} — دسترسی شیت را بررسی کنید")

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

    # 3. Public CSV fallback (no credentials) — customer-friendly
    public_read = _try_public_csv_fetch(normalized_id, sheet_name)
    if public_read is not None:
        return public_read

    # 4. All failed — return helpful message without leaking secrets
    return SourceRead(
        complete=False,
        error=(
            "no credentials configured and public fetch failed — "
            "شیت را روی 'Anyone with the link can view' بگذارید یا credentials_ref تنظیم کنید. "
            "لینک کامل را Paste کنید، ID به صورت خودکار استخراج می‌شود."
        ),
    )
