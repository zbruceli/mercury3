"""Network baseline probe (F2.5, PRD R8).

Times DNS, TCP connect, TLS handshake, and time-to-first-byte of an authenticated
`GET .../models` per provider. That endpoint is free (no tokens), so the probe measures
our network plus the provider's API gateway, not model queueing (TTFT covers that).

Uses a hand-rolled HTTP/1.1 request over asyncio streams so each phase can be timed
separately. Request headers (which carry the API key) are never returned or logged.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import os
import socket
import ssl
import statistics
import time
from collections.abc import Callable, Iterable
from typing import Protocol
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict

from dllm_eval.config import ModelRegistry, Provider, ProviderKind
from dllm_eval.timing import Clock

ANTHROPIC_BASE = "https://api.anthropic.com/v1"
ANTHROPIC_VERSION = "2023-06-01"
PHASES = ("dns_s", "connect_s", "tls_s", "ttfb_s", "total_s")


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ProbeSample(_Model):
    dns_s: float | None = None
    connect_s: float | None = None  # ~ one network round trip
    tls_s: float | None = None
    ttfb_s: float | None = None  # request sent -> first response byte (RTT + gateway)
    total_s: float | None = None
    status: int | None = None
    error: str | None = None


class ProviderProbe(_Model):
    provider: str
    host: str
    path: str
    at: str  # UTC ISO timestamp
    label: str  # "start" or "periodic"
    samples: list[ProbeSample]
    median: dict[str, float | None]

    @property
    def ok(self) -> bool:
        return any(
            s.error is None and s.status is not None and s.status < 500 for s in self.samples
        )


class Net(Protocol):
    """The three network steps, injectable so tests never touch the internet."""

    async def resolve(self, host: str, port: int) -> str: ...

    async def connect(
        self, ip: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]: ...

    async def start_tls(self, writer: asyncio.StreamWriter, host: str) -> None: ...


class AsyncioNet:
    def __init__(self, use_tls: bool = True) -> None:
        self.use_tls = use_tls
        self._ctx = ssl.create_default_context()

    async def resolve(self, host: str, port: int) -> str:
        loop = asyncio.get_running_loop()
        infos = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        return str(infos[0][4][0])

    async def connect(
        self, ip: str, port: int
    ) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        return await asyncio.open_connection(ip, port)

    async def start_tls(self, writer: asyncio.StreamWriter, host: str) -> None:
        if self.use_tls:
            await writer.start_tls(self._ctx, server_hostname=host)


def endpoint(provider: Provider) -> tuple[str, int, str] | None:
    """(host, port, path) of the provider's model-list endpoint; None if not probeable."""
    if provider.kind is ProviderKind.FAKE:
        return None
    base = ANTHROPIC_BASE if provider.kind is ProviderKind.ANTHROPIC else provider.base_url
    if not base:
        return None
    u = urlsplit(base)
    if not u.hostname:
        return None
    return u.hostname, u.port or 443, u.path.rstrip("/") + "/models"


def auth_headers(provider: Provider) -> dict[str, str]:
    key = os.environ.get(provider.api_key_env, "")
    if not key:
        return {}
    if provider.kind is ProviderKind.ANTHROPIC:
        return {"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION}
    return {"Authorization": f"Bearer {key}"}


def _status(line: bytes) -> int | None:
    parts = line.split()
    try:
        return int(parts[1]) if len(parts) >= 2 else None
    except ValueError:
        return None


async def probe_once(
    host: str,
    port: int,
    path: str,
    headers: dict[str, str],
    *,
    net: Net,
    clock: Clock = time.perf_counter,
    timeout_s: float = 10.0,
) -> ProbeSample:
    s = ProbeSample()
    t0 = clock()
    writer: asyncio.StreamWriter | None = None
    try:
        async with asyncio.timeout(timeout_s):
            ip = await net.resolve(host, port)
            t1 = clock()
            s.dns_s = t1 - t0
            reader, writer = await net.connect(ip, port)
            t2 = clock()
            s.connect_s = t2 - t1
            await net.start_tls(writer, host)
            t3 = clock()
            s.tls_s = t3 - t2
            lines = [f"GET {path} HTTP/1.1", f"Host: {host}", "Accept: application/json",
                     "User-Agent: dllm-eval-probe", "Connection: close"]  # fmt: skip
            lines += [f"{k}: {v}" for k, v in headers.items()]
            writer.write(("\r\n".join(lines) + "\r\n\r\n").encode())
            await writer.drain()
            t4 = clock()
            first = await reader.readline()
            t5 = clock()
            s.ttfb_s = t5 - t4
            s.status = _status(first)
            s.total_s = t5 - t0
    except (OSError, TimeoutError, ssl.SSLError) as e:
        s.error = f"{type(e).__name__}: {e}"[:200]
    finally:
        if writer is not None:
            writer.close()
    return s


def _median(samples: Iterable[ProbeSample]) -> dict[str, float | None]:
    good = [s for s in samples if s.error is None]
    out: dict[str, float | None] = {}
    for phase in PHASES:
        vals = [v for s in good if (v := getattr(s, phase)) is not None]
        out[phase] = statistics.median(vals) if vals else None
    return out


async def probe_provider(
    key: str,
    provider: Provider,
    *,
    samples: int = 3,
    label: str = "start",
    net: Net | None = None,
    clock: Clock = time.perf_counter,
    now: Callable[[], str] | None = None,
) -> ProviderProbe | None:
    ep = endpoint(provider)
    if ep is None:
        return None
    host, port, path = ep
    net = net or AsyncioNet()
    headers = auth_headers(provider)
    taken = [
        await probe_once(host, port, path, headers, net=net, clock=clock) for _ in range(samples)
    ]
    return ProviderProbe(
        provider=key,
        host=host,
        path=path,
        at=(now or _utc_now)(),
        label=label,
        samples=taken,
        median=_median(taken),
    )


async def probe_all(
    registry: ModelRegistry,
    provider_keys: Iterable[str],
    *,
    samples: int = 3,
    label: str = "start",
    net: Net | None = None,
) -> list[ProviderProbe]:
    """Probe providers one after another (in parallel they would compete for bandwidth)."""
    out = []
    for key in sorted(set(provider_keys)):
        p = await probe_provider(
            key, registry.providers[key], samples=samples, label=label, net=net
        )
        if p is not None:
            out.append(p)
    return out


def _utc_now() -> str:
    return dt.datetime.now(dt.UTC).isoformat(timespec="seconds")
