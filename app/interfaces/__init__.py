"""Interfaces layer — HTTP, Telegram, and Bale entrypoints.

Handlers stay thin: validate input, resolve the authenticated user and
active Business context, delegate to ``app.application`` use cases, and map
the result to a response. No business rules live here.
"""
