"""Infrastructure layer — adapters and technology-specific code.

Includes database (SQLAlchemy/Alembic), queue (Celery/Redis), platform
adapters (Telegram/Bale/Eitaa/Rubika), AI providers, and storage. Each
adapter isolates platform-specific behavior behind a capability contract
(see PLATFORM_ADAPTER_SPECIFICATION_V1).
"""
