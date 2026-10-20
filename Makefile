.PHONY: install fmt lint typecheck test test-live check validate-config

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

check: lint typecheck test validate-config
