.PHONY: install fmt lint typecheck test test-live check validate-config budget smoke-live test-sandbox

install:
	uv sync

fmt:
	uv run ruff format .
	uv run ruff check --fix .

lint:
	uv run ruff format --check .
	uv run ruff check .

typecheck:
	uv run mypy

test:
	uv run pytest --cov=dllm_eval --cov-report=term-missing

# Spends real tokens. Run only with explicit approval.
test-live:
	uv run pytest -m live

# Grades every EvalPlus canonical solution in Docker; all must pass (harness validation).
test-sandbox:
	uv run pytest -m sandbox -v

validate-config:
	uv run dllm-eval validate-config

# Offline token/cost estimate for TEST_PLAN B5 (see docs/BUDGET_ESTIMATE.md).
budget:
	uv run python scripts/estimate_budget.py configs/budget_assumptions.yaml > docs/budget_tables_full.md
	uv run python scripts/estimate_budget.py configs/budget_assumptions_lean.yaml > docs/budget_tables_lean.md

check: lint typecheck test validate-config

# R0 live smoke checks. SPENDS REAL TOKENS (~3k). Run only with explicit approval.
smoke-live:
	uv run --env-file .env python scripts/smoke_r0.py
