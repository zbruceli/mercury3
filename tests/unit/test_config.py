import copy
import datetime as dt
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError
from typer.testing import CliRunner

from dllm_eval.cli import app
from dllm_eval.config import Architecture, ModelRegistry, load_registry

REPO_MODELS = Path(__file__).parents[2] / "configs" / "models.yaml"

MINIMAL: dict[str, Any] = {
    "schema_version": 1,
    "providers": {
        "inception": {
            "kind": "openai_compat",
            "base_url": "https://api.inceptionlabs.ai/v1",
            "api_key_env": "INCEPTION_API_KEY",
            "docs_url": "https://docs.inceptionlabs.ai",
            "verified_on": "2026-10-20",
        }
    },
    "models": {
        "mercury-3": {
            "provider": "inception",
            "model_id": "mercury-3",
            "architecture": "diffusion",
            "role": "primary",
            "enabled": True,
            "pricing": {"input": 0.1, "cached_input": 0.01, "output": 0.35},
            "verified_on": "2026-10-20",
        }
    },
}


def _registry(mutate: Any = None) -> ModelRegistry:
    data = copy.deepcopy(MINIMAL)
    if mutate:
        mutate(data)
    return ModelRegistry.model_validate(data)


def test_repo_models_yaml_is_valid() -> None:
    registry = load_registry(REPO_MODELS)
    assert "mercury-3" in registry.enabled_models()
    assert registry.models["mercury-3"].architecture is Architecture.DIFFUSION


def test_repo_mercury3_matches_documented_constraints() -> None:
    m = load_registry(REPO_MODELS).models["mercury-3"]
    assert m.temperature_range == (0.5, 1.0)
    assert m.reasoning_efforts == ["instant", "low", "medium", "high"]
    assert m.max_output_tokens == 131072
    assert m.pricing is not None
    assert m.pricing_regular is not None


def test_repo_baselines_cover_prd_roles() -> None:
    enabled = load_registry(REPO_MODELS).enabled_models()
    roles = {m.role.value for m in enabled.values()}
    assert roles == {"primary", "lineage", "baseline", "hardware_control"}
    baselines = [m for m in enabled.values() if m.role.value == "baseline"]
    assert len(baselines) == 3
    assert all(m.architecture is Architecture.AUTOREGRESSIVE for m in baselines)


def test_minimal_registry_is_valid() -> None:
    assert list(_registry().enabled_models()) == ["mercury-3"]


@pytest.mark.parametrize("field", ["model_id", "pricing", "verified_on"])
def test_enabled_model_requires_field(field: str) -> None:
    with pytest.raises(ValidationError, match=field):
        _registry(lambda d: d["models"]["mercury-3"].update({field: None}))


def test_disabled_model_may_be_incomplete() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["models"]["mercury-3"].update(
            {"enabled": False, "model_id": None, "pricing": None, "verified_on": None}
        )

    assert _registry(mutate).enabled_models() == {}


def test_unknown_provider_rejected() -> None:
    with pytest.raises(ValidationError, match="unknown provider"):
        _registry(lambda d: d["models"]["mercury-3"].update({"provider": "nope"}))


def test_unknown_keys_rejected() -> None:
    with pytest.raises(ValidationError, match="typo_field"):
        _registry(lambda d: d["models"]["mercury-3"].update({"typo_field": 1}))


def test_inverted_temperature_range_rejected() -> None:
    with pytest.raises(ValidationError, match="temperature_range"):
        _registry(lambda d: d["models"]["mercury-3"].update({"temperature_range": [1.0, 0.5]}))


def test_negative_price_rejected() -> None:
    with pytest.raises(ValidationError):
        _registry(lambda d: d["models"]["mercury-3"]["pricing"].update({"output": -1}))


def test_long_context_pricing_parses() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["models"]["mercury-3"]["long_context"] = {
            "threshold_tokens": 100000,
            "pricing": {"input": 0.5, "cached_input": 0.05, "output": 2.5},
        }

    lc = _registry(mutate).models["mercury-3"].long_context
    assert lc is not None
    assert lc.threshold_tokens == 100000
    assert lc.pricing.output == 2.5


def test_rate_limits_accept_total_only() -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["providers"]["inception"]["rate_limits"] = {
            "requests_per_min": 10,
            "total_tokens_per_min": 1000,
        }

    rl = _registry(mutate).providers["inception"].rate_limits
    assert rl is not None
    assert rl.total_tokens_per_min == 1000


def test_rate_limits_require_a_token_limit() -> None:
    with pytest.raises(ValidationError, match="at least one token limit"):
        _registry(
            lambda d: d["providers"]["inception"].update({"rate_limits": {"requests_per_min": 10}})
        )


def test_stale_entries() -> None:
    reg = _registry()
    assert reg.stale_entries(dt.date(2026, 10, 27), max_age_days=7) == []
    assert reg.stale_entries(dt.date(2026, 10, 28), max_age_days=7) == [
        "model:mercury-3",
        "provider:inception",
    ]


def test_cli_validate_config_ok(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(MINIMAL))
    result = CliRunner().invoke(app, ["validate-config", "--path", str(path)])
    assert result.exit_code == 0, result.output
    assert "1 enabled" in result.output


def test_cli_validate_config_strict_fails_when_stale(tmp_path: Path) -> None:
    data = copy.deepcopy(MINIMAL)
    data["models"]["mercury-3"]["verified_on"] = "2020-01-01"
    path = tmp_path / "models.yaml"
    path.write_text(yaml.safe_dump(data))
    result = CliRunner().invoke(app, ["validate-config", "--path", str(path), "--strict"])
    assert result.exit_code == 1
    assert "model:mercury-3" in result.output


def test_cli_validate_config_invalid(tmp_path: Path) -> None:
    path = tmp_path / "models.yaml"
    path.write_text("schema_version: 1\nproviders: {}\nmodels: {x: {}}\n")
    result = CliRunner().invoke(app, ["validate-config", "--path", str(path)])
    assert result.exit_code == 1
