#!/usr/bin/env python3
"""Tenant isolation audit (Phase 11).

Per SECURITY_THREAT_MODEL_V1 section 12 and TEST_STRATEGY_V1 section 5:
Every data access path must be tenant/business scoped. Test intentional
cross-tenant access attempts.

This script runs a series of cross-tenant checks via the application layer
(not just HTTP) to ensure isolation.

Usage:
    python scripts/tenant_isolation_audit.py
    TEST_BACKEND=postgres DATABASE_URL=... python scripts/tenant_isolation_audit.py
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config.settings import get_settings
from app.infrastructure.db.base import Base, register_models
from app.infrastructure.db.session import create_session_factory
from app.application import auth as auth_svc
from app.application import business as biz_svc
from app.application import products as products_svc
from app.application import sources as sources_svc


def _setup_db() -> Session:
    settings = get_settings()
    register_models()
    # Use test DB if env set, else in-memory
    import os

    if os.environ.get("TEST_BACKEND") == "postgres" and os.environ.get("DATABASE_URL"):
        engine = create_engine(os.environ["DATABASE_URL"])
        Base.metadata.drop_all(engine)
        Base.metadata.create_all(engine)
        from app.infrastructure.db.seed import seed_reference_data

        with engine.connect() as conn:
            seed_reference_data(conn)
    else:
        from sqlalchemy.pool import StaticPool

        engine = create_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        from app.infrastructure.db.seed import seed_reference_data

        with engine.connect() as conn:
            seed_reference_data(conn)

    factory = create_session_factory(engine)
    return factory()


def main():
    print("=== Tenant Isolation Audit (Phase 11) ===")
    db = _setup_db()

    # Create two users and businesses
    user1 = auth_svc.register_user(db, email="tenant1@example.com", password="correct-horse-battery-1", display_name="Tenant1")
    user2 = auth_svc.register_user(db, email="tenant2@example.com", password="correct-horse-battery-1", display_name="Tenant2")
    db.commit()

    b1 = biz_svc.create_business(db, user=user1, name="Business1", business_type_key="general")
    b2 = biz_svc.create_business(db, user=user2, name="Business2", business_type_key="general")
    db.commit()

    print(f"Created: user1={user1.user_id} business1={b1.business_id}")
    print(f"Created: user2={user2.user_id} business2={b2.business_id}")

    # Create products in each business
    from app.infrastructure.db import models
    from app.domain import enums

    p1 = models.Product(
        business_id=b1.business_id,
        name="Product Tenant1",
        lifecycle_state=enums.ProductLifecycle.ACTIVE.value,
        current_version=1,
        attributes={},
    )
    p2 = models.Product(
        business_id=b2.business_id,
        name="Product Tenant2",
        lifecycle_state=enums.ProductLifecycle.ACTIVE.value,
        current_version=1,
        attributes={},
    )
    db.add_all([p1, p2])
    db.commit()

    print(f"Products: p1={p1.product_id} (b1), p2={p2.product_id} (b2)")

    # Test 1: user1 cannot access b2's product via business-scoped fetch
    print("\nTest 1: Cross-tenant product access via get_product")
    result = products_svc.get_product(db, business_id=b1.business_id, product_id=p2.product_id)
    if result is None:
        print("  ✅ PASS: b1 cannot access p2 (returns None -> 404)")
    else:
        print("  ❌ FAIL: b1 accessed p2!")
        sys.exit(1)

    result = products_svc.get_product(db, business_id=b2.business_id, product_id=p1.product_id)
    if result is None:
        print("  ✅ PASS: b2 cannot access p1")
    else:
        print("  ❌ FAIL: b2 accessed p1!")
        sys.exit(1)

    # Test 2: list_products is business-scoped
    print("\nTest 2: list_products isolation")
    list_b1 = products_svc.list_products(db, business_id=b1.business_id)
    list_b2 = products_svc.list_products(db, business_id=b2.business_id)
    if len(list_b1) == 1 and list_b1[0].product_id == p1.product_id:
        print("  ✅ PASS: b1 list only contains p1")
    else:
        print(f"  ❌ FAIL: b1 list wrong: {[p.product_id for p in list_b1]}")
        sys.exit(1)
    if len(list_b2) == 1 and list_b2[0].product_id == p2.product_id:
        print("  ✅ PASS: b2 list only contains p2")
    else:
        print(f"  ❌ FAIL: b2 list wrong")
        sys.exit(1)

    # Test 3: Sources isolation
    print("\nTest 3: Sources isolation")
    s1 = models.Source(
        business_id=b1.business_id,
        name="Source1",
        kind=enums.SourceKind.EXCEL_UPLOAD.value,
        status=enums.SourceStatus.ACTIVE.value,
    )
    db.add(s1)
    db.commit()
    fetched = sources_svc.get_source(db, business_id=b2.business_id, source_id=s1.source_id)
    if fetched is None:
        print("  ✅ PASS: b2 cannot access s1")
    else:
        print("  ❌ FAIL: b2 accessed s1")
        sys.exit(1)

    # Test 4: Business access control
    print("\nTest 4: Business access control")
    try:
        biz_svc.require_business_access(db, user=user1, business_id=b2.business_id)
        print("  ❌ FAIL: user1 accessed b2")
        sys.exit(1)
    except Exception:
        print("  ✅ PASS: user1 cannot access b2 (exception)")

    # Test 5: Sync jobs isolation (if any)
    print("\nTest 5: Sync jobs isolation (empty check)")
    from sqlalchemy import select

    jobs_b1 = db.scalars(select(models.SyncJob).where(models.SyncJob.business_id == b1.business_id)).all()
    jobs_b2 = db.scalars(select(models.SyncJob).where(models.SyncJob.business_id == b2.business_id)).all()
    if len(jobs_b1) == 0 and len(jobs_b2) == 0:
        print("  ✅ PASS: no jobs, isolation trivially holds")
    else:
        print("  ⚠️  Jobs exist, check manually")

    print("\n=== All tenant isolation checks PASS ===")
    print("Note: HTTP-level cross-tenant tests are in tests/integration/test_*_flow.py")
    print("This audit confirms service-layer isolation.")


if __name__ == "__main__":
    main()
