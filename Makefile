# Result Guardian — NODE A task runner.
# Windows dev boxes without `make` use ./tasks.ps1, which mirrors these.

COMPOSE ?= docker compose

.PHONY: help up down logs build ps health psql dx migrate revision test lint fmt typecheck check nodeb-up clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS=":.*?## "}; {printf "  \033[36m%-14s\033[0m %s\n", $$1, $$2}'

up:         ## Build and start the NODE A stack
	$(COMPOSE) up -d --build

down:       ## Stop the stack (data kept; use `make clean` to wipe)
	$(COMPOSE) down

logs:       ## Tail api + worker logs
	$(COMPOSE) logs -f api worker

ps:         ## Show service status
	$(COMPOSE) ps

health:     ## Hit /api/health
	curl -s http://localhost/api/health

dx:         ## List installed Postgres extensions
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-rg_app} -d $${POSTGRES_DB:-result_guardian} -c '\dx'

psql:       ## Open a psql shell
	$(COMPOSE) exec postgres psql -U $${POSTGRES_USER:-rg_app} -d $${POSTGRES_DB:-result_guardian}

migrate:    ## Apply migrations
	$(COMPOSE) exec api alembic upgrade head

revision:   ## New migration:  make revision m="add patients"
	$(COMPOSE) exec api alembic revision --autogenerate -m "$(m)"

test:       ## Run tests (must pass with NODE B unreachable)
	$(COMPOSE) run --rm api pytest

lint:       ## ruff + black --check
	$(COMPOSE) run --rm api sh -c "ruff check . && black --check ."

fmt:        ## Format in place
	$(COMPOSE) run --rm api sh -c "ruff check --fix . && black ."

typecheck:  ## mypy
	$(COMPOSE) run --rm api mypy app worker

check: lint typecheck test  ## Everything CI runs

nodeb-up:   ## Start Ollama on NODE B (run on NODE B, not NODE A)
	$(COMPOSE) -f docker-compose.nodeb.yml up -d

clean:      ## Stop and DELETE all data
	$(COMPOSE) down -v
