from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from app.application.sync_jobs import begin, enqueue, finish, retry_or_exhaust
from app.domain import enums
from app.domain.sync_policy import SyncPolicy


def _job(*, status=enums.SyncJobStatus.QUEUED.value, attempts=0):
    return SimpleNamespace(
        status=status,
        attempt_count=attempts,
        max_attempts=3,
        started_at=None,
        heartbeat_at=None,
        next_retry_at=None,
        finished_at=None,
        failure_summary=None,
        counts={},
        row_errors=[],
    )


def test_scheduled_excel_sync_is_rejected_before_queueing():
    source = SimpleNamespace(
        source_id="source-1",
        business_id="business-1",
        kind=enums.SourceKind.EXCEL_UPLOAD.value,
    )

    with pytest.raises(ValueError, match="only for Google Sheets"):
        enqueue(
            None,
            source=source,
            mapping=None,
            trigger=enums.SyncTrigger.SCHEDULED,
            requested_by=None,
            correlation_id="corr-1",
        )


def test_begin_claims_queued_job_once_and_increments_attempt():
    job = _job()

    assert begin(None, job) is True
    assert job.status == enums.SyncJobStatus.RUNNING.value
    assert job.attempt_count == 1
    assert job.heartbeat_at is not None
    assert begin(None, job) is False
    assert job.attempt_count == 1


def test_retry_waits_with_backoff_before_exhaustion():
    now = datetime(2026, 9, 16, tzinfo=UTC)
    job = _job(status=enums.SyncJobStatus.RUNNING.value, attempts=1)

    allowed = retry_or_exhaust(None, job, error="temporary", policy=SyncPolicy(), now=now)

    assert allowed is True
    assert job.status == enums.SyncJobStatus.RETRY_WAITING.value
    assert job.next_retry_at == datetime(2026, 9, 16, 0, 1, tzinfo=UTC)
    assert job.failure_summary == "temporary"


def test_third_failure_is_final_and_keeps_diagnostics():
    now = datetime(2026, 9, 16, tzinfo=UTC)
    job = _job(status=enums.SyncJobStatus.RUNNING.value, attempts=3)

    allowed = retry_or_exhaust(None, job, error="source unavailable", now=now)

    assert allowed is False
    assert job.status == enums.SyncJobStatus.FAILED_RETRY_EXHAUSTED.value
    assert job.finished_at == now
    assert job.failure_summary == "source unavailable"


def test_success_with_row_errors_is_not_reported_as_clean_success():
    job = _job(status=enums.SyncJobStatus.RUNNING.value, attempts=1)

    finish(job, success=True, counts={"blank": 2, "invalid": 1}, row_errors=[{"locator": "row:7"}])

    assert job.status == enums.SyncJobStatus.SUCCEEDED_WITH_ERRORS.value
    assert job.counts["blank"] == 2
    assert job.row_errors[0]["locator"] == "row:7"


def test_failed_execution_requires_recovery():
    job = _job(status=enums.SyncJobStatus.RUNNING.value, attempts=1)

    finish(job, success=False, counts={}, row_errors=[])

    assert job.status == enums.SyncJobStatus.RECOVERY_REQUIRED.value
