"""AI credit ledger (spec section 9).

Monthly and purchased pools are SEPARATE ledgers. Consumption is atomic
(single transaction; pool rows locked with FOR UPDATE on PostgreSQL) and
idempotent (a repeated call with the same idempotency key returns the
original outcome without double-consuming). Every movement is an
append-only ledger row (auditable credit history).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.application.audit import AuditService
from app.domain import enums
from app.domain.errors import CreditInsufficient, ValidationError
from app.infrastructure.db.models import CreditPool, CreditTransaction

# Deduction priority: the monthly grant is used before purchased credits,
# so purchased top-ups survive across months.
_POOL_PRIORITY: tuple[enums.CreditPoolType, ...] = (
    enums.CreditPoolType.MONTHLY,
    enums.CreditPoolType.PURCHASED,
)


def _pool(
    db: Session,
    *,
    business_id: uuid.UUID,
    pool_type: enums.CreditPoolType,
    period_label: str,
) -> CreditPool | None:
    return db.scalar(
        select(CreditPool).where(
            CreditPool.business_id == business_id,
            CreditPool.pool_type == pool_type.value,
            CreditPool.period_label == period_label,
        )
    )


def grant_monthly_pool(
    db: Session,
    *,
    business_id: uuid.UUID,
    period_label: str,
    amount: int,
    actor_user_id: uuid.UUID | None = None,
) -> CreditPool:
    """Grant (idempotently) the monthly pool for a period.

    If the pool for (business, MONTHLY, label) already exists it is
    returned unchanged — re-granting the same period never doubles credits.
    """
    if amount < 0:
        raise ValidationError("Grant amount must be non-negative")
    pool = _pool(
        db,
        business_id=business_id,
        pool_type=enums.CreditPoolType.MONTHLY,
        period_label=period_label,
    )
    if pool is not None:
        return pool
    pool = CreditPool(
        business_id=business_id,
        pool_type=enums.CreditPoolType.MONTHLY.value,
        period_label=period_label,
        granted_total=amount,
        remaining=amount,
    )
    db.add(pool)
    db.flush()
    db.add(
        CreditTransaction(
            pool_id=pool.pool_id,
            business_id=business_id,
            direction=enums.CreditDirection.GRANT.value,
            amount=amount,
            idempotency_key=f"grant-monthly:{business_id}:{period_label}",
            actor_user_id=actor_user_id,
            meta_data={"reason": "billing period grant"},
        )
    )
    AuditService(db).record(
        action="credit.granted",
        actor_user_id=actor_user_id,
        business_id=business_id,
        target_type="credit_pool",
        target_id=pool.pool_id,
        meta={"pool_type": "MONTHLY", "period": period_label, "amount": amount},
    )
    return pool


def topup_purchased_pool(
    db: Session,
    *,
    business_id: uuid.UUID,
    amount: int,
    note: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> CreditPool:
    """Operator top-up of the non-expiring purchased pool."""
    if amount <= 0:
        raise ValidationError("Top-up amount must be positive")
    pool = _pool(
        db,
        business_id=business_id,
        pool_type=enums.CreditPoolType.PURCHASED,
        period_label=enums.PURCHASED_PERIOD_LABEL,
    )
    if pool is None:
        pool = CreditPool(
            business_id=business_id,
            pool_type=enums.CreditPoolType.PURCHASED.value,
            period_label=enums.PURCHASED_PERIOD_LABEL,
            granted_total=amount,
            remaining=amount,
        )
        db.add(pool)
    else:
        pool.granted_total += amount
        pool.remaining += amount
    db.flush()
    db.add(
        CreditTransaction(
            pool_id=pool.pool_id,
            business_id=business_id,
            direction=enums.CreditDirection.GRANT.value,
            amount=amount,
            reference=note,
            actor_user_id=actor_user_id,
            meta_data={"reason": "operator top-up", "note": note},
        )
    )
    AuditService(db).record(
        action="credit.topup",
        actor_user_id=actor_user_id,
        business_id=business_id,
        target_type="credit_pool",
        target_id=pool.pool_id,
        meta={"pool_type": "PURCHASED", "amount": amount, "note": note},
    )
    return pool


def consume_credit(
    db: Session,
    *,
    business_id: uuid.UUID,
    amount: int,
    idempotency_key: str,
    reference: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Sequence[CreditTransaction]:
    """Atomically consume credits; idempotent per ``idempotency_key``.

    Reusing a key returns the original transaction(s) — the second call
    never deducts again. Monthly credits are consumed before purchased.
    """
    if amount <= 0:
        raise ValidationError("Consumption amount must be positive")
    if not idempotency_key or len(idempotency_key) > 120:
        raise ValidationError("idempotency_key (1-120 chars) is required")

    existing = db.scalars(
        select(CreditTransaction).where(
            CreditTransaction.business_id == business_id,
            CreditTransaction.idempotency_key == idempotency_key,
        )
    ).all()
    if existing:
        return list(existing)

    # Lock the pools that participate, in a stable order (deadlock-free).
    pools = db.scalars(
        select(CreditPool)
        .where(
            CreditPool.business_id == business_id,
            CreditPool.pool_type.in_(
                [t.value for t in _POOL_PRIORITY],
            ),
        )
        .with_for_update()
        .order_by(CreditPool.pool_type, CreditPool.period_label)
    ).all()
    ordered = {t: [p for p in pools if p.pool_type == t.value] for t in _POOL_PRIORITY}

    available = sum(p.remaining for p in pools)
    if available < amount:
        AuditService(db).record(
            action="credit.insufficient",
            outcome=enums.AuditOutcome.FAILURE,
            actor_user_id=actor_user_id,
            business_id=business_id,
            meta={"requested": amount, "available": available},
        )
        raise CreditInsufficient(requested=amount, available=available)

    txs: list[CreditTransaction] = []
    remaining_to_deduct = amount
    first = True
    for pool_type in _POOL_PRIORITY:
        for pool in ordered[pool_type]:
            if remaining_to_deduct <= 0:
                break
            take = min(pool.remaining, remaining_to_deduct)
            if take <= 0:
                continue
            pool.remaining -= take
            remaining_to_deduct -= take
            txs.append(
                CreditTransaction(
                    pool_id=pool.pool_id,
                    business_id=business_id,
                    direction=enums.CreditDirection.CONSUME.value,
                    amount=take,
                    # Only the first row of a multi-pool operation carries
                    # the key (unique column); the rest link via reference.
                    idempotency_key=idempotency_key if first else None,
                    reference=reference or (idempotency_key if not first else None),
                    actor_user_id=actor_user_id,
                )
            )
            first = False
    db.add_all(txs)
    db.flush()
    AuditService(db).record(
        action="credit.consumed",
        actor_user_id=actor_user_id,
        business_id=business_id,
        target_type="credit",
        target_id=idempotency_key,
        meta={"amount": amount, "pools_touched": len(txs)},
    )
    return txs


def refund_credit(
    db: Session,
    *,
    business_id: uuid.UUID,
    amount: int,
    idempotency_key: str,
    reference: str | None = None,
    actor_user_id: uuid.UUID | None = None,
) -> Sequence[CreditTransaction]:
    """Return credits to the purchased pool (operator remediation path).

    Refunds go to PURCHASED (not MONTHLY) so they are not lost when the
    monthly period rolls over. Idempotent per key.
    """
    if amount <= 0:
        raise ValidationError("Refund amount must be positive")
    if not idempotency_key or len(idempotency_key) > 120:
        raise ValidationError("idempotency_key (1-120 chars) is required")

    existing = db.scalars(
        select(CreditTransaction).where(
            CreditTransaction.business_id == business_id,
            CreditTransaction.idempotency_key == idempotency_key,
        )
    ).all()
    if existing:
        return list(existing)

    pool = _pool(
        db,
        business_id=business_id,
        pool_type=enums.CreditPoolType.PURCHASED,
        period_label=enums.PURCHASED_PERIOD_LABEL,
    )
    if pool is None:
        pool = CreditPool(
            business_id=business_id,
            pool_type=enums.CreditPoolType.PURCHASED.value,
            period_label=enums.PURCHASED_PERIOD_LABEL,
            granted_total=0,
            remaining=0,
        )
        db.add(pool)
    pool.remaining += amount
    db.flush()
    tx = CreditTransaction(
        pool_id=pool.pool_id,
        business_id=business_id,
        direction=enums.CreditDirection.REFUND.value,
        amount=amount,
        idempotency_key=idempotency_key,
        reference=reference,
        actor_user_id=actor_user_id,
    )
    db.add(tx)
    db.flush()
    AuditService(db).record(
        action="credit.refunded",
        actor_user_id=actor_user_id,
        business_id=business_id,
        target_type="credit_pool",
        target_id=pool.pool_id,
        meta={"amount": amount},
    )
    return [tx]


def expire_monthly_pool(
    db: Session,
    *,
    business_id: uuid.UUID,
    period_label: str,
    actor_user_id: uuid.UUID | None = None,
) -> CreditTransaction | None:
    """Zero out an unused monthly pool at period rollover (audited)."""
    pool = _pool(
        db,
        business_id=business_id,
        pool_type=enums.CreditPoolType.MONTHLY,
        period_label=period_label,
    )
    if pool is None or pool.remaining <= 0:
        return None
    remaining = pool.remaining
    pool.remaining = 0
    db.flush()
    tx = CreditTransaction(
        pool_id=pool.pool_id,
        business_id=business_id,
        direction=enums.CreditDirection.EXPIRE.value,
        amount=remaining,
        actor_user_id=actor_user_id,
        meta_data={"period": period_label},
    )
    db.add(tx)
    db.flush()
    AuditService(db).record(
        action="credit.expired",
        actor_user_id=actor_user_id,
        business_id=business_id,
        target_type="credit_pool",
        target_id=pool.pool_id,
        meta={"period": period_label, "amount": remaining},
    )
    return tx


def list_pools(db: Session, *, business_id: uuid.UUID) -> Sequence[CreditPool]:
    return list(
        db.scalars(
            select(CreditPool)
            .where(CreditPool.business_id == business_id)
            .order_by(CreditPool.pool_type, CreditPool.period_label)
        ).all()
    )


def list_transactions(
    db: Session, *, business_id: uuid.UUID, limit: int = 50
) -> Sequence[CreditTransaction]:
    return list(
        db.scalars(
            select(CreditTransaction)
            .where(CreditTransaction.business_id == business_id)
            .order_by(CreditTransaction.created_at.desc(), CreditTransaction.tx_id.desc())
            .limit(limit)
        ).all()
    )
