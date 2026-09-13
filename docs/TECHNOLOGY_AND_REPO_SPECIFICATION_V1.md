# Technology & Repository Specification v1
# دستیار هوشمند کسب‌وکارهای مجازی
Status: Proposed stack — owner approval required before implementation
Date: 2026-09-12

## 1. Goals
Choose a mature, testable, maintainable stack that can begin on a weak VPS and scale without a core rewrite.

## 2. Proposed backend
- Python 3.13.x stable line (conservative production choice; Python 3.14 is current stable but dependency compatibility should be rechecked before adoption).
- FastAPI for HTTP/API/Web endpoints.
- SQLAlchemy 2.0.x stable line for ORM/Core.
- PostgreSQL 18 for durable relational state.
- Alembic for schema migrations.
- Pydantic v2 for validated DTO/configuration.
- httpx for async external APIs.

Rationale: Python is already the user's strongest practical language and the domain is highly I/O-oriented. SQLAlchemy 2.0.x remains the current stable release line as of 2026-09-12; 2.1 is still release-candidate status, so production V1 should prefer 2.0.x until 2.1 is finalized and tested. citeturn656215search0turn656215search3

## 3. Background jobs
- Redis as broker/cache.
- Celery workers + Celery Beat for asynchronous jobs and schedules.
- PostgreSQL remains the source of truth for job/business state; Celery task state is operational, not authoritative.
- Use queue priorities, per-platform throttling, retry policy, deduplication and explicit dead/final states.

## 4. Web UI
Proposed lightweight V1:
- Server-rendered FastAPI/Jinja2 pages
- HTMX for interactive actions/partial updates
- small JavaScript layer only where needed
- Nginx reverse proxy/static serving
This avoids a second permanent Node runtime and keeps the starting server footprint small. A SPA can be introduced later if the product's UI complexity justifies it.

## 5. Messaging interfaces
- Telegram adapter/interface
- Bale adapter/interface
- Eitaa and Rubika adapters only after platform certification
All interfaces call the same application/use-case layer.

## 6. Data ingestion
- openpyxl for XLSX
- Official Google Sheets API via google-auth / google-api-python-client or equivalent well-maintained official client
- batch reads for efficiency where appropriate; Google explicitly recommends batchGet/batchUpdate for multiple ranges. citeturn926801search0turn926801search2

## 7. AI provider
Use a Provider interface. The first provider can be an external API, but the domain must not depend on one vendor or model. Provider/model/prompt definitions are configuration-driven.

## 8. Storage
PostgreSQL for durable relational state.
Object/media storage may start on local disk through an abstraction, but storage must be replaceable by S3-compatible/object storage later.

## 9. Observability
Structured application logs + metrics + health endpoints. The exact vendor/tool is an implementation choice after the first deployment test; do not overprovision the weak server.

## 10. Docker
Docker Compose for initial single-server deployment. Containers are separated so workers, web and infrastructure can scale independently later.

## 11. Dependency policy
- Pin exact production versions after compatibility verification.
- Maintain a lock file.
- Upgrade one dependency family at a time.
- Run full regression, failure and integration tests before upgrades.
- Never upgrade all foundational packages blindly.

## 12. Database version note
PostgreSQL 18 is currently supported, while 17 is also supported. PostgreSQL's policy recommends current minor releases for supported branches. citeturn656215search5
V1 may use PostgreSQL 18 after integration validation; PostgreSQL 17 is a conservative fallback if an operational dependency requires it.

## 13. Repository structure
```text
project/
  app/
    domain/
    application/
    infrastructure/
    interfaces/
    workers/
    config/
  tests/
    unit/
    integration/
    contract/
    e2e/
    failure/
  docs/
    domain/
    architecture/
    runbooks/
  scripts/
  migrations/
  deploy/
  templates/
  static/
  backups/
```
The exact package/module tree is finalized during implementation planning, not guessed ad hoc.

## 14. Prohibited architecture shortcuts
- business logic inside Telegram handlers
- duplicated business logic for Bale/Web
- direct SQL from interface code
- global mutable state for job/business state
- one infinite sync loop per customer
- storing critical state only in Redis/cache
- platform-specific rules scattered through domain services
