"""Subscription state machine (pure domain): transitions, calendar, periods."""

from __future__ import annotations

from datetime import datetime

import pytest
from app.domain import enums
from app.domain.entitlements import (
    ALLOWED_TRANSITIONS,
    add_months,
    can_transition,
    is_live,
    is_terminal,
    period_label,
    period_months,
)

S = enums.SubscriptionStatus


@pytest.mark.parametrize(
    ("current", "target"),
    [
        (S.PENDING, S.ACTIVE),
        (S.PENDING, S.CANCELLED),
        (S.ACTIVE, S.GRACE),
        (S.ACTIVE, S.SUSPENDED),
        (S.ACTIVE, S.CANCELLED),
        (S.ACTIVE, S.REFUNDED),
        (S.GRACE, S.ACTIVE),
        (S.GRACE, S.EXPIRED),
        (S.GRACE, S.SUSPENDED),
        (S.GRACE, S.CANCELLED),
        (S.GRACE, S.REFUNDED),
        (S.SUSPENDED, S.ACTIVE),
        (S.SUSPENDED, S.GRACE),
        (S.SUSPENDED, S.CANCELLED),
        (S.SUSPENDED, S.REFUNDED),
    ],
)
def test_allowed_transitions_accepted(current: S, target: S) -> None:
    assert can_transition(current, target) is True


def test_full_matrix_no_unlisted_transition() -> None:
    """Every (current, target) pair is allowed iff explicitly listed."""
    for current in S:
        for target in S:
            expected = target in ALLOWED_TRANSITIONS[current]
            assert can_transition(current, target) is expected, (current, target)


@pytest.mark.parametrize("terminal", [S.EXPIRED, S.CANCELLED, S.REFUNDED])
def test_terminal_states_never_transition(terminal: S) -> None:
    assert is_terminal(terminal) is True
    for target in S:
        assert can_transition(terminal, target) is False


def test_non_terminal_slot_membership() -> None:
    assert {s for s in S if is_live(s)} == enums.SUBSCRIPTION_NON_TERMINAL
    assert is_live(S.ACTIVE) and is_live(S.PENDING)
    assert not is_live(S.EXPIRED) and not is_live(S.CANCELLED)


def test_period_months() -> None:
    assert period_months("monthly") == 1
    with pytest.raises(ValueError):
        period_months("yearly")


def test_add_months_clamps_day() -> None:
    assert add_months(datetime(2026, 1, 31), 1) == datetime(2026, 2, 28)
    assert add_months(datetime(2024, 1, 31), 1) == datetime(2024, 2, 29)  # leap
    assert add_months(datetime(2026, 2, 28), 1) == datetime(2026, 3, 28)


def test_add_months_wraps_years() -> None:
    assert add_months(datetime(2026, 11, 15), 4) == datetime(2027, 3, 15)
    assert add_months(datetime(2026, 12, 31), 12) == datetime(2027, 12, 31)


def test_period_label() -> None:
    assert period_label(datetime(2026, 9, 13)) == "2026-09"
    assert period_label(datetime(2027, 1, 1)) == "2027-01"
