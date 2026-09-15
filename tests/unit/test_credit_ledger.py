"""Credit ledger unit tests: atomicity, idempotency, pool priority, expiry.

Runs on the in-memory SQLite engine (locally) or PostgreSQL (CI) via the
shared db_session fixture — both must uphold the ledger invariants.
"""

from __future__ import annotations

import uuid

import pytest
from app.application import credits
from app.domain import enums
from app.domain.errors import CreditInsufficient, ValidationError
from app.infrastructure.db.models import CreditPool, CreditTransaction
from sqlalchemy import select
from sqlalchemy.orm import Session


@pytest.fixture()
def business(db_session: Session) -> uuid.UUID:
    """A business row (credit pools reference it)."""
    from app.application import auth
    from app.infrastructure.db.models import Business

    auth.register_user(
        db_session,
        email="credit-test@example.com",
        password="correct-horse-battery-1",
        display_name="CT",
    )
    b = Business(business_name="Credit Co", business_type_key="general")
    db_session.add(b)
    db_session.flush()
    db_session.commit()
    return b.business_id


def test_grant_monthly_pool_is_idempotent(db_session: Session, business: uuid.UUID) -> None:
    p1 = credits.grant_monthly_pool(
        db_session, business_id=business, period_label="2026-09", amount=50
    )
    db_session.commit()
    p2 = credits.grant_monthly_pool(
        db_session, business_id=business, period_label="2026-09", amount=50
    )
    db_session.commit()
    assert p1.pool_id == p2.pool_id
    assert p2.remaining == 50  # not doubled
    grants = db_session.scalars(
        select(CreditTransaction).where(
            CreditTransaction.business_id == business,
            CreditTransaction.direction == enums.CreditDirection.GRANT.value,
        )
    ).all()
    assert len(grants) == 1


def test_consume_prefers_monthly_over_purchased(db_session: Session, business: uuid.UUID) -> None:
    credits.grant_monthly_pool(db_session, business_id=business, period_label="2026-09", amount=30)
    credits.topup_purchased_pool(db_session, business_id=business, amount=100)
    db_session.commit()

    credits.consume_credit(db_session, business_id=business, amount=80, idempotency_key="c1")
    db_session.commit()

    monthly = db_session.scalar(
        select(CreditPool).where(
            CreditPool.business_id == business,
            CreditPool.pool_type == enums.CreditPoolType.MONTHLY.value,
        )
    )
    purchased = db_session.scalar(
        select(CreditPool).where(
            CreditPool.business_id == business,
            CreditPool.pool_type == enums.CreditPoolType.PURCHASED.value,
        )
    )
    assert monthly.remaining == 0  # monthly drained first
    assert purchased.remaining == 50  # then purchased (100-50)


def test_consume_is_idempotent_per_key(db_session: Session, business: uuid.UUID) -> None:
    credits.grant_monthly_pool(db_session, business_id=business, period_label="2026-09", amount=100)
    db_session.commit()

    first = credits.consume_credit(
        db_session, business_id=business, amount=40, idempotency_key="k-dup"
    )
    db_session.commit()
    second = credits.consume_credit(
        db_session, business_id=business, amount=40, idempotency_key="k-dup"
    )
    db_session.commit()

    assert [t.tx_id for t in first] == [t.tx_id for t in second]  # same original tx
    pool = db_session.scalar(select(CreditPool).where(CreditPool.business_id == business))
    assert pool.remaining == 60  # deducted exactly once


def test_consume_insufficient_raises_and_preserves_state(
    db_session: Session, business: uuid.UUID
) -> None:
    credits.grant_monthly_pool(db_session, business_id=business, period_label="2026-09", amount=10)
    db_session.commit()

    with pytest.raises(CreditInsufficient) as excinfo:
        credits.consume_credit(db_session, business_id=business, amount=11, idempotency_key="k-no")
    assert excinfo.value.requested == 11
    assert excinfo.value.available == 10
    db_session.rollback()

    pool = db_session.scalar(select(CreditPool).where(CreditPool.business_id == business))
    assert pool.remaining == 10  # unchanged
    fails = db_session.scalars(
        select(CreditTransaction)
        .where(CreditTransaction.business_id == business)
    ).all()
    # No consume transaction was written for the failed attempt.
    assert all(t.direction != enums.CreditDirection.CONSUME.value for t in fails)


def test_consume_requires_valid_inputs(db_session: Session, business: uuid.UUID) -> None:
    credits.grant_monthly_pool(db_session, business_id=business, period_label="2026-09", amount=10)
    db_session.commit()
    with pytest.raises(ValidationError):
        credits.consume_credit(db_session, business_id=business, amount=0, idempotency_key="k0")
    with pytest.raises(ValidationError):
        credits.consume_credit(db_session, business_id=business, amount=5, idempotency_key="")


def test_refund_goes_to_purchased_pool(db_session: Session, business: uuid.UUID) -> None:
    credits.grant_monthly_pool(db_session, business_id=business, period_label="2026-09", amount=10)
    credits.consume_credit(
        db_session, business_id=business, amount=10, idempotency_key="r-src"
    )
    db_session.commit()

    credits.refund_credit(db_session, business_id=business, amount=6, idempotency_key="r-1")
    credits.refund_credit(db_session, business_id=business, amount=6, idempotency_key="r-1")
    db_session.commit()

    purchased = db_session.scalar(
        select(CreditPool).where(
            CreditPool.business_id == business,
            CreditPool.pool_type == enums.CreditPoolType.PURCHASED.value,
        )
    )
    assert purchased is not None
    assert purchased.remaining == 6  # idempotent refund


def test_topup_purchased_pool_upserts(db_session: Session, business: uuid.UUID) -> None:
    credits.topup_purchased_pool(db_session, business_id=business, amount=100, note="first")
    db_session.commit()
    credits.topup_purchased_pool(db_session, business_id=business, amount=50, note="second")
    db_session.commit()

    pool = db_session.scalar(
        select(CreditPool).where(
            CreditPool.business_id == business,
            CreditPool.pool_type == enums.CreditPoolType.PURCHASED.value,
        )
    )
    assert pool.granted_total == 150
    assert pool.remaining == 150


def test_expire_monthly_pool_zeroes_and_audits(db_session: Session, business: uuid.UUID) -> None:
    credits.grant_monthly_pool(
        db_session, business_id=business, period_label="2026-08", amount=40
    )
    credits.consume_credit(db_session, business_id=business, amount=15, idempotency_key="e-c")
    db_session.commit()

    tx = credits.expire_monthly_pool(db_session, business_id=business, period_label="2026-08")
    db_session.commit()
    assert tx is not None and tx.amount == 25

    pool = db_session.scalar(
        select(CreditPool).where(
            CreditPool.business_id == business,
            CreditPool.pool_type == enums.CreditPoolType.MONTHLY.value,
        )
    )
    assert pool.remaining == 0

    # Expiring an already-zero pool is a no-op.
    again = credits.expire_monthly_pool(
        db_session, business_id=business, period_label="2026-08"
    )
    assert again is None
