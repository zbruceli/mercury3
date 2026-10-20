.PHONY: install fmt lint typecheck test test-live check validate-config budget

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

validate-config:
	uv run dllm-eval validate-config

# Offline token/cost estimate for TEST_PLAN B5 (see docs/BUDGET_ESTIMATE.md).
budget:
	uv run python scripts/estimate_budget.py configs/budget_assumptions.yaml > docs/budget_tables_full.md
	uv run python scripts/estimate_budget.py configs/budget_assumptions_lean.yaml > docs/budget_tables_lean.md

check: lint typecheck test validate-config
