"""دستیار هوشمند کسب‌وکارهای مجازی — application root package.

Layered architecture (see TECHNOLOGY_AND_REPO_SPECIFICATION_V1):
- app.domain: pure domain contracts (no framework imports)
- app.application: use cases / services orchestrating domain + ports
- app.infrastructure: adapters (DB, queue, platforms, AI providers, storage)
- app.interfaces: HTTP / Telegram / Bale entrypoints (thin handlers)
- app.workers: Celery tasks and scheduling
- app.config: settings and configuration

No business logic may be added to this baseline beyond the documented
Phase 0 infrastructure. Domain implementation starts in Phase 1.
"""

__version__ = "0.1.0"
