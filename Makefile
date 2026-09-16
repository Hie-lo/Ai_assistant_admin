# دستیار هوشمند کسب‌وکارهای مجازی — developer shortcuts
.PHONY: install lint format test test-integration compose-up compose-down migrate-rev migrate-up

install:            ## Install runtime + dev dependencies
	pip install -r requirements-dev.txt

lint:               ## Ruff check
	ruff check app tests migrations

format:             ## Ruff format
	ruff format app tests migrations

test:               ## Unit tests (no services required)
	pytest tests/unit

test-integration:   ## Integration tests (requires local docker compose up)
	pytest tests/integration -m integration

compose-up:         ## Start postgres + redis + web + worker
	docker compose up -d --build

compose-down:       ## Stop the stack
	docker compose down

migrate-rev:        ## autogenerate a migration: make migrate-rev m="description"
	alembic revision --autogenerate -m "$(m)"

migrate-up:         ## Apply migrations
	alembic upgrade head
