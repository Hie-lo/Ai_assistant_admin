"""Unit tests for Sync Jobs API contract (Phase 8)."""

import uuid
from types import SimpleNamespace

import pytest
from app.application.sync_jobs import enqueue
from app.domain import enums


def test_excel_scheduled_sync_rejected():
    source = SimpleNamespace(
        source_id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        kind=enums.SourceKind.EXCEL_UPLOAD.value,
    )
    with pytest.raises(ValueError, match="only for Google Sheets"):
        enqueue(
            None,
            source=source,
            mapping=None,
            trigger=enums.SyncTrigger.SCHEDULED,
            requested_by=None,
            correlation_id="test",
        )


def test_excel_automatic_sync_rejected():
    source = SimpleNamespace(
        source_id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        kind=enums.SourceKind.EXCEL_UPLOAD.value,
    )
    with pytest.raises(ValueError, match="only for Google Sheets"):
        enqueue(
            None,
            source=source,
            mapping=None,
            trigger=enums.SyncTrigger.EVENT_ASSISTED,
            requested_by=None,
            correlation_id="test",
        )


def test_google_sheets_manual_allowed():
    # This should not raise ValueError for trigger check (other checks may fail due to None db)
    # We test the boundary condition only
    source = SimpleNamespace(
        source_id=uuid.uuid4(),
        business_id=uuid.uuid4(),
        kind=enums.SourceKind.GOOGLE_SHEETS.value,
    )
    # The function will try to query db and fail, but not due to Excel boundary
    try:
        enqueue(
            None,
            source=source,
            mapping=None,
            trigger=enums.SyncTrigger.SCHEDULED,
            requested_by=None,
            correlation_id="test",
        )
    except ValueError as exc:
        # Should NOT be the Excel boundary error
        assert "only for Google Sheets" not in str(exc)
    except Exception:
        # Expected due to None db, but boundary passed
        pass
