"""F2.5 network probe, fully offline: scripted fake network plus one loopback-only test."""

import asyncio
import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from typer.testing import CliRunner

from dllm_eval import cli
from dllm_eval.config import ModelRegistry
from dllm_eval.experiment import Experiment
from dllm_eval.logging_store import RunStore
from dllm_eval.probe import AsyncioNet, auth_headers, endpoint, probe_once, probe_provider
from dllm_eval.runner import run_experiment
from tests.conftest import FAKE_REGISTRY
from tests.unit.test_runner import EXP


class _Writer:
    def __init__(self) -> None:
        self.sent = b""
        self.closed = False

    def write(self, data: bytes) -> None:
        self.sent += data

    async def drain(self) -> None:
        pass

    def close(self) -> None:
        self.closed = True


class FakeNet:
    def __init__(self, response: bytes = b"HTTP/1.1 200 OK\r\n", fail: str | None = None) -> None:
        self.response = response
        self.fail = fail
        self.writer = _Writer()

    async def resolve(self, host: str, port: int) -> str:
        if self.fail == "dns":
            raise OSError("no such host")
        return "203.0.113.7"

    async def connect(self, ip: str, port: int) -> tuple[Any, Any]:
        if self.fail == "hang":
            await asyncio.sleep(10)
        reader = asyncio.StreamReader()
        reader.feed_data(self.response)
        reader.feed_eof()
        return reader, self.writer

    async def start_tls(self, writer: Any, host: str) -> None:
        pass


def ticking(step: float = 0.01) -> Any:
    t = [0.0]

    def clock() -> float:
        t[0] += step
        return t[0]

    return clock


def test_probe_once_times_each_phase() -> None:
    net = FakeNet()
    s = asyncio.run(
        probe_once(
            "api.example.test",
            443,
            "/v1/models",
            {"Authorization": "Bearer k"},
            net=net,
            clock=ticking(),
        )
    )
    assert s.error is None and s.status == 200
    for phase in (s.dns_s, s.connect_s, s.tls_s, s.ttfb_s):
        assert phase == pytest.approx(0.01)
    assert s.total_s == pytest.approx(0.05)
    sent = net.writer.sent.decode()
    assert sent.startswith("GET /v1/models HTTP/1.1\r\nHost: api.example.test\r\n")
    assert "Authorization: Bearer k" in sent and sent.endswith("\r\n\r\n")
    assert net.writer.closed
    assert "Bearer" not in s.model_dump_json()  # headers never end up in the record


def test_probe_once_records_errors_instead_of_raising() -> None:
    s = asyncio.run(probe_once("h", 443, "/", {}, net=FakeNet(fail="dns")))
    assert s.error and "no such host" in s.error
    assert s.dns_s is None and s.status is None


def test_probe_once_times_out() -> None:
    s = asyncio.run(probe_once("h", 443, "/", {}, net=FakeNet(fail="hang"), timeout_s=0.05))
    assert s.error and s.error.startswith("TimeoutError")


def test_probe_records_401_as_reachable() -> None:
    s = asyncio.run(probe_once("h", 443, "/", {}, net=FakeNet(b"HTTP/1.1 401 Unauthorized\r\n")))
    assert s.status == 401 and s.error is None


def test_endpoints_from_registry(
    repo_registry: ModelRegistry, fake_registry: ModelRegistry
) -> None:
    p = repo_registry.providers
    assert endpoint(p["inception"]) == ("api.inceptionlabs.ai", 443, "/v1/models")
    assert endpoint(p["google"]) == (
        "generativelanguage.googleapis.com",
        443,
        "/v1beta/openai/models",
    )
    assert endpoint(p["cerebras"]) == ("api.cerebras.ai", 443, "/v1/models")
    assert endpoint(p["anthropic"]) == ("api.anthropic.com", 443, "/v1/models")
    assert endpoint(fake_registry.providers["fakeco"]) is None


def test_auth_headers(repo_registry: ModelRegistry, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "a-key")
    monkeypatch.setenv("CEREBRAS_API_KEY", "c-key")
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    assert auth_headers(repo_registry.providers["anthropic"]) == {
        "x-api-key": "a-key",
        "anthropic-version": "2023-06-01",
    }
    assert auth_headers(repo_registry.providers["cerebras"]) == {"Authorization": "Bearer c-key"}
    assert auth_headers(repo_registry.providers["openai"]) == {}


def test_probe_provider_takes_medians(repo_registry: ModelRegistry) -> None:
    pr = asyncio.run(
        probe_provider(
            "inception",
            repo_registry.providers["inception"],
            samples=3,
            net=FakeNet(),
            now=lambda: "T",
        )
    )
    assert pr is not None and pr.ok
    assert len(pr.samples) == 3 and pr.at == "T" and pr.label == "start"
    assert pr.median["connect_s"] is not None


def test_loopback_server_real_sockets() -> None:
    """Real asyncio streams against 127.0.0.1 (no TLS, no internet)."""

    async def go() -> Any:
        async def handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
            await reader.readuntil(b"\r\n\r\n")
            writer.write(b"HTTP/1.1 200 OK\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()

        server = await asyncio.start_server(handle, "127.0.0.1", 0)
        port = server.sockets[0].getsockname()[1]
        async with server:
            return await probe_once(
                "127.0.0.1", port, "/v1/models", {}, net=AsyncioNet(use_tls=False)
            )

    s = asyncio.run(go())
    assert s.error is None and s.status == 200
    assert s.total_s is not None and s.total_s >= 0


# --- runner: periodic re-probe -----------------------------------------------------


def _slow_exp(**overrides: Any) -> Experiment:
    d = copy.deepcopy(EXP)
    d["repeats"] = 1
    d["max_concurrency_per_provider"] = 1
    d["network_probe_interval_s"] = 0.02
    for arm in d["arms"]:
        arm["provider_extras"] = {"fake_delay_s": 0.02}
    d.update(overrides)
    return Experiment.model_validate(d)


def test_runner_reprobes_periodically(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    labels: list[str] = []

    async def prober(label: str) -> list[dict[str, Any]]:
        labels.append(label)
        return [{"provider": "fakeco", "label": label}]

    store = RunStore(tmp_path / "run")
    store.create({"run_id": "r"})
    s = asyncio.run(
        run_experiment(_slow_exp(), fake_registry, store, "r", prober=prober, log=lambda _: None)
    )
    assert s.ok == 10
    assert labels and set(labels) == {"periodic"}
    assert [p["label"] for p in store.iter_probes()] == labels


def test_failed_reprobe_does_not_stop_the_run(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    async def prober(label: str) -> list[dict[str, Any]]:
        raise OSError("network down")

    logs: list[str] = []
    store = RunStore(tmp_path / "run")
    store.create({"run_id": "r"})
    s = asyncio.run(
        run_experiment(_slow_exp(), fake_registry, store, "r", prober=prober, log=logs.append)
    )
    assert s.ok == 10 and s.aborted is None
    assert any("network probe failed" in line for line in logs)


def test_no_periodic_probe_without_interval(fake_registry: ModelRegistry, tmp_path: Path) -> None:
    called: list[str] = []

    async def prober(label: str) -> list[dict[str, Any]]:
        called.append(label)
        return []

    store = RunStore(tmp_path / "run")
    store.create({"run_id": "r"})
    asyncio.run(
        run_experiment(
            _slow_exp(network_probe_interval_s=None),
            fake_registry,
            store,
            "r",
            prober=prober,
            log=lambda _: None,
        )
    )
    assert called == []


# --- CLI: clean-tree guard + start probe in the manifest ---------------------------


@pytest.fixture
def files(tmp_path: Path) -> tuple[Path, Path, Path]:
    models = tmp_path / "models.yaml"
    models.write_text(yaml.safe_dump(FAKE_REGISTRY))
    cfg = tmp_path / "exp.yaml"
    cfg.write_text(yaml.safe_dump(EXP))
    return models, cfg, tmp_path / "runs"


def _live(files: tuple[Path, Path, Path], *extra: str) -> Any:
    models, cfg, runs = files
    return CliRunner().invoke(
        cli.app,
        ["run", str(cfg), "--models", str(models), "--runs-dir", str(runs), "--live", *extra],
    )


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"sha": "abc", "dirty": True}, "uncommitted changes"),
        ({"sha": None, "dirty": None}, "not a git checkout"),
    ],
)
def test_live_refuses_dirty_tree(
    files: tuple[Path, Path, Path],
    monkeypatch: pytest.MonkeyPatch,
    state: dict[str, Any],
    reason: str,
) -> None:
    monkeypatch.setattr(cli, "git_state", lambda _root: state)
    r = _live(files)
    assert r.exit_code == 1
    assert reason in r.output and "--allow-dirty" in r.output
    assert not files[2].exists()  # nothing created


def test_allow_dirty_is_recorded(
    files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "git_state", lambda _root: {"sha": "abc", "dirty": True})
    r = _live(files, "--allow-dirty")
    assert r.exit_code == 0, r.output
    [run_dir] = list(files[2].iterdir())
    git = json.loads((run_dir / "manifest.json").read_text())["git"]
    assert git == {"sha": "abc", "dirty": True, "allow_dirty": True}


def test_start_probe_goes_into_manifest_and_probes_log(
    files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from dllm_eval.probe import ProbeSample, ProviderProbe

    monkeypatch.setattr(cli, "git_state", lambda _root: {"sha": "abc", "dirty": False})
    sample = ProbeSample(
        dns_s=0.001, connect_s=0.020, tls_s=0.030, ttfb_s=0.050, total_s=0.101, status=200
    )

    async def fake_probe_all(
        registry: Any, keys: Any, *, samples: int, label: str
    ) -> list[ProviderProbe]:
        return [ProviderProbe(provider="fakeco", host="h", path="/v1/models", at="T", label=label,
                              samples=[sample], median=sample.model_dump(exclude={"status", "error"}))]  # fmt: skip

    monkeypatch.setattr(cli, "probe_all", fake_probe_all)
    r = _live(files)
    assert r.exit_code == 0, r.output
    assert "network probe fakeco: connect 20 ms, ttfb 50 ms" in r.output
    [run_dir] = list(files[2].iterdir())
    manifest = json.loads((run_dir / "manifest.json").read_text())
    assert manifest["git"]["allow_dirty"] is False
    assert manifest["network_probe"][0]["label"] == "start"
    assert [p["label"] for p in RunStore(run_dir).iter_probes()] == ["start"]

    a = CliRunner().invoke(
        cli.app,
        ["analyze", run_dir.name, "--runs-dir", str(files[2]), "--out-dir", str(tmp_path / "an")],
    )
    assert a.exit_code == 0, a.output
    assert (
        "Network baseline" in a.output
        and "| fakeco | T | start | 1/1 | 1 | 20 | 30 | 50 | 101 |" in a.output
    )


def test_probe_can_be_disabled(
    files: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cli, "git_state", lambda _root: {"sha": "abc", "dirty": False})

    async def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("probe should not run")

    monkeypatch.setattr(cli, "probe_all", boom)
    d = copy.deepcopy(EXP)
    d["network_probe"] = False
    files[1].write_text(yaml.safe_dump(d))
    r = _live(files)
    assert r.exit_code == 0, r.output


def test_probe_all_skips_unprobeable_and_sorts(repo_registry: ModelRegistry) -> None:
    from dllm_eval.probe import probe_all

    out = asyncio.run(
        probe_all(repo_registry, ["inception", "google", "inception"], samples=2, net=FakeNet())
    )
    assert [p.provider for p in out] == ["google", "inception"]  # deduped, sorted, sequential
    assert all(len(p.samples) == 2 and p.ok for p in out)

    merged = ModelRegistry.model_validate(
        {
            **FAKE_REGISTRY,
            "providers": {
                **FAKE_REGISTRY["providers"],
                **{k: v.model_dump() for k, v in repo_registry.providers.items()},
            },
        }
    )
    assert asyncio.run(probe_all(merged, ["fakeco"], net=FakeNet())) == []
