from datetime import timedelta

import pytest

from app.domain.sync_policy import SyncPolicy


def test_default_policy_is_three_attempts_with_bounded_exponential_backoff():
    policy = SyncPolicy()

    assert policy.max_attempts == 3
    assert policy.backoff(1) == timedelta(seconds=60)
    assert policy.backoff(2) == timedelta(seconds=120)
    assert policy.backoff(3) == timedelta(seconds=240)
    assert policy.exhausted(2) is False
    assert policy.exhausted(3) is True


def test_backoff_is_capped():
    policy = SyncPolicy(backoff_base_seconds=10, backoff_max_seconds=25)

    assert policy.backoff(1) == timedelta(seconds=10)
    assert policy.backoff(2) == timedelta(seconds=20)
    assert policy.backoff(3) == timedelta(seconds=25)
    assert policy.backoff(10) == timedelta(seconds=25)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"max_attempts": 0},
        {"backoff_base_seconds": -1},
        {"backoff_max_seconds": 5, "backoff_base_seconds": 10},
        {"stale_after_seconds": 0},
    ],
)
def test_invalid_policy_is_rejected(kwargs):
    with pytest.raises(ValueError):
        SyncPolicy(**kwargs)


def test_invalid_attempt_is_rejected():
    with pytest.raises(ValueError):
        SyncPolicy().backoff(0)
