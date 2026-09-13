"""Platform adapters (Phase 5: Telegram; Phase 6: Bale; Phase 7: Eitaa/Rubika).

Each adapter declares capabilities/limits (adapter spec section 3) and
normalizes platform errors into the shared taxonomy, so the core never
hard-codes platform behavior (spec section 8: no silent capability
fallback).
"""
