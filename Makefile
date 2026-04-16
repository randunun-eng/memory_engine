.DEFAULT_GOAL := help
.PHONY: help install sync test test-unit test-integration test-invariants test-eval \
        lint fmt typecheck check clean migrate status serve docs-check doctor

help:  ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
	 awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# -------- Setup --------

install:  ## Initial setup — install uv deps and pre-commit hooks
	uv sync --all-extras
	uv pip install --system sqlite-vec
	uv run pre-commit install

sync:  ## Update uv dependencies after a lock change
	uv sync --all-extras

# -------- Tests --------

test:  ## Run all tests except eval (unit + integration + invariants)
	uv run pytest tests/unit tests/integration tests/invariants -v

test-unit:  ## Unit tests only (fast)
	uv run pytest tests/unit -v

test-integration:  ## Integration tests (in-memory SQLite)
	uv run pytest tests/integration -v

test-invariants:  ## Governance-rule invariants
	uv run pytest tests/invariants -v

test-eval:  ## Slow evaluation tests (MRR, accept rate; excluded from default)
	uv run pytest tests/eval --eval -v

cov:  ## Test with coverage report
	uv run pytest tests/unit tests/integration tests/invariants \
	    --cov=memory_engine --cov-report=term-missing --cov-report=html

# -------- Lint / types --------

lint:  ## Ruff lint
	uv run ruff check

fmt:  ## Ruff format (applies changes)
	uv run ruff format
	uv run ruff check --fix

typecheck:  ## mypy in strict mode
	uv run mypy src/

check: lint typecheck test  ## Everything CI runs

# -------- DB / operations --------

migrate:  ## Apply pending database migrations
	uv run memory-engine db migrate

status:  ## Show applied migrations
	uv run memory-engine db status

doctor:  ## Run engine health checks (Phase 3+)
	uv run memory-engine doctor

# -------- Serve --------

serve:  ## Start the HTTP server + background loops
	uv run memory-engine serve

serve-dev:  ## Serve with debug logging and autoreload
	MEMORY_ENGINE_LOG_LEVEL=DEBUG uv run uvicorn memory_engine.http.app:app --reload

# -------- Examples --------

example-phase0:  ## Run the Phase 0 round-trip demo
	uv run python examples/phase0_round_trip.py

# -------- Docs --------

docs-check:  ## Lint markdown links and basic structure
	@command -v markdownlint-cli2 >/dev/null 2>&1 && \
	  markdownlint-cli2 "**/*.md" || \
	  echo "markdownlint-cli2 not installed; skipping (install via: npm i -g markdownlint-cli2)"

diagrams-export:  ## Export Mermaid diagrams to SVG (needs mmdc)
	@command -v mmdc >/dev/null 2>&1 && \
	  mmdc -i docs/diagrams/README.md -o docs/diagrams/exports/ || \
	  echo "mermaid-cli not installed; install with: npm i -g @mermaid-js/mermaid-cli"

# -------- Clean --------

clean:  ## Remove build artefacts and caches
	find . -type d -name __pycache__ -exec rm -rf {} +
	find . -type d -name .pytest_cache -exec rm -rf {} +
	find . -type d -name .mypy_cache -exec rm -rf {} +
	find . -type d -name .ruff_cache -exec rm -rf {} +
	find . -type d -name "*.egg-info" -exec rm -rf {} +
	rm -rf .coverage htmlcov/ dist/ build/

clean-db:  ## Delete the local development database (DANGEROUS)
	@read -p "Delete data/engine.db? [y/N] " confirm && [ "$$confirm" = "y" ]
	rm -f data/engine.db data/engine.db-journal data/engine.db-wal data/engine.db-shm
