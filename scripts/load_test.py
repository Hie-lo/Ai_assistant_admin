#!/usr/bin/env python3
"""Load test script (Phase 11 — Scale hardening).

Per TEST_STRATEGY_V1 section 6: measure import throughput, DB latency,
queue latency, publish throughput, AI throughput, memory, CPU, concurrent tenants.

This is a simple HTTP load tester (no external deps) that hits health,
auth, business, product endpoints.

Usage:
    python scripts/load_test.py --url http://localhost:8000 --concurrency 10 --requests 100
    python scripts/load_test.py --url https://yourdomain.com --auth-token <token> --business-id <uuid>
"""

from __future__ import annotations

import argparse
import concurrent.futures
import statistics
import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))


def _request(url: str, path: str, token: str | None = None) -> tuple[int, float, str]:
    import urllib.request
    import urllib.error

    full = f"{url.rstrip('/')}{path}"
    start = time.perf_counter()
    try:
        req = urllib.request.Request(full)
        if token:
            req.add_header("Cookie", f"ai_session={token}")
        req.add_header("X-Correlation-Id", uuid.uuid4().hex)
        with urllib.request.urlopen(req, timeout=10) as resp:
            status = resp.status
            body = resp.read(200)
            duration = (time.perf_counter() - start) * 1000
            return status, duration, ""
    except urllib.error.HTTPError as e:
        duration = (time.perf_counter() - start) * 1000
        return e.code, duration, str(e)[:100]
    except Exception as exc:
        duration = (time.perf_counter() - start) * 1000
        return 0, duration, f"{type(exc).__name__}: {exc}"[:200]


def main():
    parser = argparse.ArgumentParser(description="Simple load test")
    parser.add_argument("--url", default="http://localhost:8000", help="Base URL")
    parser.add_argument("--concurrency", type=int, default=10, help="Concurrent workers")
    parser.add_argument("--requests", type=int, default=100, help="Total requests")
    parser.add_argument("--auth-token", help="Session token for authenticated tests")
    parser.add_argument("--business-id", help="Business ID for product tests")

    args = parser.parse_args()

    print(f"Load test: {args.url} — {args.concurrency} concurrency, {args.requests} requests")

    paths = [
        "/healthz",
        "/readyz",
        "/api/v1/business-types",
    ]
    if args.business_id and args.auth_token:
        paths.extend(
            [
                f"/api/v1/businesses/{args.business_id}/products",
                f"/api/v1/businesses/{args.business_id}/sources",
                f"/api/v1/businesses/{args.business_id}/sync-jobs",
            ]
        )

    # Warmup
    print("Warmup...")
    for p in paths[:2]:
        _request(args.url, p)

    # Run
    print(f"Running {args.requests} requests across {len(paths)} paths...")
    start_total = time.perf_counter()
    results: list[tuple[str, int, float, str]] = []

    def task(path: str):
        return (path, *_request(args.url, path, args.auth_token))

    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as executor:
        futures = []
        for i in range(args.requests):
            path = paths[i % len(paths)]
            futures.append(executor.submit(task, path))
        for f in concurrent.futures.as_completed(futures):
            results.append(f.result())

    total_duration = time.perf_counter() - start_total

    # Analyze
    by_path: dict[str, list[float]] = {}
    status_counts: dict[int, int] = {}
    errors = []

    for path, status, duration, err in results:
        by_path.setdefault(path, []).append(duration)
        status_counts[status] = status_counts.get(status, 0) + 1
        if status >= 400 or status == 0:
            errors.append((path, status, err))

    print("\n=== Results ===")
    print(f"Total time: {total_duration:.2f}s")
    print(f"Requests: {len(results)}")
    print(f"RPS: {len(results)/total_duration:.1f}")
    print("\nStatus codes:")
    for code, count in sorted(status_counts.items()):
        print(f"  {code}: {count}")

    print("\nLatency by path (ms):")
    for path, durations in by_path.items():
        if durations:
            print(
                f"  {path}: avg={statistics.mean(durations):.1f} p50={statistics.median(durations):.1f} "
                f"p95={sorted(durations)[int(len(durations)*0.95)]:.1f} max={max(durations):.1f} count={len(durations)}"
            )

    if errors:
        print(f"\nErrors ({len(errors)}):")
        for path, status, err in errors[:10]:
            print(f"  {path} -> {status}: {err}")

    # Thresholds (Phase 11: measure, not guess)
    print("\n=== Thresholds (informational) ===")
    print("- /healthz avg < 100ms expected")
    print("- /readyz avg < 200ms expected (includes DB check)")
    print("- Authenticated product list avg < 500ms expected")
    print("- Error rate < 1% expected")

    # Simple pass/fail
    avg_health = statistics.mean(by_path.get("/healthz", [0])) if by_path.get("/healthz") else 0
    if avg_health > 500:
        print("\n⚠️  /healthz latency high — check server resources")
    if status_counts.get(0, 0) > 0:
        print("\n❌ Connection errors — server may be down")
        sys.exit(1)
    if sum(1 for _, s, _, _ in results if s >= 500) > len(results) * 0.05:
        print("\n❌ >5% 5xx errors — check logs")
        sys.exit(1)

    print("\n✅ Load test completed")


if __name__ == "__main__":
    main()
