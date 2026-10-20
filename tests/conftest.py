from pathlib import Path
from typing import Any

import pytest

from dllm_eval.config import ModelRegistry, load_registry

REPO = Path(__file__).parents[1]
RECORDINGS = REPO / "tests" / "fixtures" / "recordings"

FAKE_REGISTRY: dict[str, Any] = {
    "schema_version": 1,
    "providers": {
        "fakeco": {
            "kind": "fake",
            "api_key_env": "FAKE_API_KEY",
            "docs_url": "https://example.invalid",
            "rate_limits": {"requests_per_min": 10000, "total_tokens_per_min": 100000000},
            "verified_on": "2026-10-20",
        }
    },
    "models": {
        "fake-diffusion": {
            "provider": "fakeco",
            "model_id": "fake-d",
            "architecture": "diffusion",
            "role": "primary",
            "enabled": True,
            "max_output_tokens": 1000,
            "temperature_range": [0.5, 1.0],
            "reasoning_efforts": ["instant", "low"],
            "supports": {"streaming": True, "tools": True, "structured_outputs": True},
            "pricing": {"input": 1.0, "cached_input": 0.1, "output": 2.0},
            "verified_on": "2026-10-20",
        },
        "fake-ar": {
            "provider": "fakeco",
            "model_id": "fake-a",
            "architecture": "autoregressive",
            "role": "baseline",
            "enabled": True,
            "max_output_tokens": 1000,
            "reasoning_efforts": ["none", "medium"],
            "supports": {"streaming": True, "tools": True, "structured_outputs": True},
            "pricing": {"input": 0.5, "cached_input": 0.05, "output": 1.0},
            "verified_on": "2026-10-20",
        },
    },
}


@pytest.fixture(autouse=True)
def _offline_cli(monkeypatch: pytest.MonkeyPatch) -> None:
    """Safety net for every test: the CLI sees a clean git tree and never probes the network.
    Tests of the guard or the probe override these with their own monkeypatch."""
    from dllm_eval import cli

    async def no_probe(*args: Any, **kwargs: Any) -> list[Any]:
        return []

    monkeypatch.setattr(cli, "git_state", lambda _root: {"sha": "test", "dirty": False})
    monkeypatch.setattr(cli, "probe_all", no_probe)


@pytest.fixture
def fake_registry() -> ModelRegistry:
    return ModelRegistry.model_validate(FAKE_REGISTRY)


@pytest.fixture(scope="session")
def repo_registry() -> ModelRegistry:
    return load_registry(REPO / "configs" / "models.yaml")
