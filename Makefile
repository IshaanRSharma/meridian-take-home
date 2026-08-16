# Meridian — dev commands.
#
# Targets are added by the unit that makes them real. `migrate`, `seed` and
# `types` arrive with units 2 and 12; a target that echoes "not implemented"
# is worse than one that does not exist yet.

SHELL := /bin/bash
UV    := cd meridian && uv run

# The database integration tests run against. Matches Settings.test_database_url
# so `make db` and the suite agree without either reading the other's config.
TEST_DB := postgresql://meridian:meridian@localhost:54329/meridian?sslmode=disable

.DEFAULT_GOAL := help
.PHONY: help install fmt lint typecheck test cov check db temporal down clean

help: ## List available targets
	@grep -hE '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

# ── python ───────────────────────────────────────────────────────────────
install: ## Create the venv and install the package plus dev tools
	cd meridian && uv sync

fmt: ## Format and apply safe lint fixes
	$(UV) ruff format .
	$(UV) ruff check --fix .

lint: ## Lint and verify formatting (no writes)
	$(UV) ruff check .
	$(UV) ruff format --check .

typecheck: ## mypy --strict
	$(UV) mypy

test: ## Run the test suite
	$(UV) pytest

migrate: ## Apply db/migrations to DATABASE_URL
	$(UV) python -m meridian.migrate

seed: ## Load the pre-alert board into $DATABASE_URL
	$(UV) python -m meridian.seed

cov: ## Run tests with a coverage report
	$(UV) pytest --cov --cov-report=term-missing

check: lint typecheck test ## Everything CI would run

# ── local services ───────────────────────────────────────────────────────
db: ## Start local Postgres 17 for integration tests and migrate it (port 54329)
	docker compose up -d postgres
	@until docker compose exec -T postgres pg_isready -U meridian -d meridian >/dev/null 2>&1; \
		do sleep 1; done
	docker compose exec -T postgres psql -qU meridian -d meridian -v ON_ERROR_STOP=1 \
		< meridian/db/bootstrap.sql
	cd meridian && DATABASE_URL=$(TEST_DB) uv run python -m meridian.migrate

temporal: ## Start the Temporal dev server (gRPC 7233, UI http://localhost:8233)
	docker compose up -d temporal

down: ## Stop local services
	docker compose down

clean: ## Remove caches and build artefacts
	find . -name __pycache__ -type d -prune -exec rm -rf {} +
	rm -rf meridian/.pytest_cache meridian/.mypy_cache meridian/.ruff_cache
	rm -rf meridian/.coverage meridian/htmlcov
