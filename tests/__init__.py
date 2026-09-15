"""Test package.

Layers (per TEST_STRATEGY_V1):
- unit: pure domain/functions, no services
- integration: PostgreSQL, Redis, adapters
- contract: platform/AI adapter contracts
- e2e: critical journeys interface -> durable state -> side effects
- failure: crash/restart/timeout/duplicate/recovery
"""
