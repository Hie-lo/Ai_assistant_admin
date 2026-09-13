"""Unit tests for the Google Sheets adapter (injectable fake client).

No network and no real OAuth: the client factory is injected, which also
proves the production seam (credentials are never logged, errors are
reported as incomplete reads).
"""

from __future__ import annotations

import pytest
from app.infrastructure.sources import google_sheets
from app.infrastructure.sources.google_sheets import read_spreadsheet

pytestmark = pytest.mark.unit


class FakeGetResponse:
    def __init__(self, values: list[list[str]]) -> None:
        self._values = values

    def execute(self) -> dict:
        return {"values": self._values}


class FakeValues:
    def __init__(self, values: list[list[str]], raise_exc: bool = False) -> None:
        self._values = values
        self._raise = raise_exc
        self.requested_range: str | None = None
        self.requested_spreadsheet: str | None = None

    def get(self, spreadsheetId: str, range: str | None = None) -> FakeGetResponse:
        self.requested_range = range
        self.requested_spreadsheet = spreadsheetId
        if self._raise:
            raise RuntimeError("permission denied")
        return FakeGetResponse(self._values)


class FakeService:
    def __init__(self, values: list[list[str]], raise_exc: bool = False) -> None:
        self._values_obj = FakeValues(values, raise_exc)

    def values(self) -> FakeValues:
        return self._values_obj


class _FakeSource:
    kind = "GOOGLE_SHEETS"

    def __init__(self, credentials_ref: str | None = "sa-1") -> None:
        self.credentials_ref = credentials_ref
        self.external_ref = "SPREADSHEET-1"


def _install(service: FakeService) -> None:
    google_sheets.set_client_factory(lambda _ref: service)


@pytest.fixture(autouse=True)
def _reset_factory() -> None:
    google_sheets.set_client_factory(None)
    yield
    google_sheets.set_client_factory(None)


def test_read_with_range_from_sheet_name() -> None:
    service = FakeService(
        [
            ["Name", "Price"],
            ["A", "1000"],
            ["", ""],  # empty row skipped
            ["B", "2000"],
        ]
    )
    _install(service)
    read = read_spreadsheet(
        _FakeSource(), spreadsheet_id="SPREADSHEET-1", sheet_name="Products", range_spec=None
    )
    assert read.complete is True
    assert service.values().requested_spreadsheet == "SPREADSHEET-1"
    assert service.values().requested_range == "'Products'"
    assert read.headers == ["Name", "Price"]
    assert read.row_count == 2
    assert read.rows[1]["Name"] == "B"


def test_explicit_range_spec_wins() -> None:
    service = FakeService([["Name", "Price"], ["A", "1"]])
    _install(service)
    read = read_spreadsheet(
        _FakeSource(), spreadsheet_id="S", sheet_name="Products", range_spec="Products!A1:C50"
    )
    assert read.complete is True
    assert service.values().requested_range == "Products!A1:C50"


def test_no_sheet_no_range_reads_default() -> None:
    service = FakeService([["Name", "Price"], ["A", "1"]])
    _install(service)
    read = read_spreadsheet(_FakeSource(), spreadsheet_id="S", sheet_name=None, range_spec=None)
    assert read.complete is True
    assert service.values().requested_range == "A1:ZZ"


def test_api_error_reports_incomplete_never_raises() -> None:
    _install(FakeService([], raise_exc=True))
    read = read_spreadsheet(_FakeSource(), spreadsheet_id="S", sheet_name=None, range_spec=None)
    assert read.complete is False
    assert "api read failed" in (read.error or "")
    assert read.rows == []


def test_missing_spreadsheet_id() -> None:
    read = read_spreadsheet(_FakeSource(), spreadsheet_id="", sheet_name=None, range_spec=None)
    assert read.complete is False
    assert read.error is not None


def test_no_factory_and_no_credentials_fails_closed() -> None:
    read = read_spreadsheet(
        _FakeSource(credentials_ref=None), spreadsheet_id="S", sheet_name=None, range_spec=None
    )
    assert read.complete is False
    assert "no credentials" in (read.error or "")


def test_header_row_after_blank_rows() -> None:
    service = FakeService([["", ""], ["", ""], ["Name", "Price"], ["A", "1"]])
    _install(service)
    read = read_spreadsheet(_FakeSource(), spreadsheet_id="S", sheet_name=None, range_spec=None)
    assert read.complete is True
    assert read.headers == ["Name", "Price"]
    assert read.row_count == 1


def test_no_header_reports_incomplete() -> None:
    service = FakeService([["only-one"], ["x"]])
    _install(service)
    read = read_spreadsheet(_FakeSource(), spreadsheet_id="S", sheet_name=None, range_spec=None)
    assert read.complete is False
