"""Comportamiento del registro en Consul mediante su frontera HTTP."""

from __future__ import annotations

import asyncio
import json
import re
from urllib.error import URLError
from urllib.request import Request

import pytest

from app.core.config import Settings
from app.core.consul import ConsulRegistrar


class FakeResponse:
    """Respuesta HTTP mínima compatible con el context manager de urllib."""

    status = 200

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_args: object) -> None:
        return None


def consul_settings(**overrides: object) -> Settings:
    """Configuración determinista para las pruebas del agente Consul."""
    values: dict[str, object] = {
        "consul_http_addr": "http://consul.test:8500",
        "service_name": "persistencia",
        "service_port": 8000,
        "service_id": "persistencia-test1234",
        "advertise_addr": "10.20.30.40",
        "consul_enabled": True,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)


def test_settings_include_consul_registration_defaults() -> None:
    settings = Settings(_env_file=None)

    assert settings.consul_http_addr == "http://consul:8500"
    assert settings.service_name == "persistencia"
    assert settings.service_port == 8000
    assert settings.service_id is None
    assert settings.advertise_addr is None
    assert settings.consul_enabled is True


async def test_register_sends_service_check_and_read_only_traefik_tags(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        assert timeout > 0
        captured.append(request)
        return FakeResponse()

    from app.core import consul

    monkeypatch.setattr(consul, "urlopen", fake_urlopen)
    registrar = ConsulRegistrar(consul_settings())

    await registrar.register()

    assert len(captured) == 1
    request = captured[0]
    assert request.full_url == "http://consul.test:8500/v1/agent/service/register"
    assert request.get_method() == "PUT"
    registration = json.loads(request.data or b"{}")
    assert registration["Name"] == "persistencia"
    assert registration["ID"] == "persistencia-test1234"
    assert registration["Address"] == "10.20.30.40"
    assert registration["Port"] == 8000
    assert registration["Check"] == {
        "HTTP": "http://10.20.30.40:8000/ready",
        "Interval": "3s",
        "Timeout": "1s",
        "DeregisterCriticalServiceAfter": "1m",
    }
    assert set(registration["Tags"]) == {
        "traefik.enable=true",
        "traefik.http.routers.persistencia.rule=Host(`persistencia.universidad.localhost`) && Method(`GET`)",
        "traefik.http.routers.persistencia.entrypoints=https",
        "traefik.http.routers.persistencia.tls=true",
        "traefik.http.routers.persistencia.middlewares=persistencia-retry",
        "traefik.http.middlewares.persistencia-retry.retry.attempts=2",
        "traefik.http.middlewares.persistencia-retry.retry.initialinterval=500ms",
        "traefik.http.services.persistencia.loadbalancer.server.port=8000",
    }


async def test_register_fails_explicitly_when_consul_api_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import consul

    def unavailable(_request: Request, timeout: float) -> FakeResponse:
        raise URLError("Consul is unavailable")

    monkeypatch.setattr(consul, "urlopen", unavailable)

    with pytest.raises(URLError, match="Consul is unavailable"):
        await ConsulRegistrar(consul_settings()).register()


async def test_registration_retries_in_background_until_consul_recovers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from app.core import consul

    attempts: list[str] = []
    registered = asyncio.Event()

    def flaky_urlopen(request: Request, timeout: float) -> FakeResponse:
        attempts.append(request.full_url)
        if len(attempts) == 1:
            raise URLError("Consul is unavailable")
        registered.set()
        return FakeResponse()

    monkeypatch.setattr(consul, "urlopen", flaky_urlopen)
    monkeypatch.setattr(consul, "INITIAL_RETRY_DELAY", 0.01)

    registrar = ConsulRegistrar(consul_settings())
    await registrar.start()
    await asyncio.wait_for(registered.wait(), timeout=1)
    await registrar.stop()

    assert attempts[0].endswith("/v1/agent/service/register")
    assert attempts[1].endswith("/v1/agent/service/register")
    assert attempts[2].endswith("/v1/agent/service/deregister/persistencia-test1234")


async def test_deregister_uses_the_service_id_in_the_agent_api_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        captured.append(request)
        return FakeResponse()

    from app.core import consul

    monkeypatch.setattr(consul, "urlopen", fake_urlopen)

    await ConsulRegistrar(consul_settings()).deregister()

    assert len(captured) == 1
    assert captured[0].full_url.endswith("/v1/agent/service/deregister/persistencia-test1234")
    assert captured[0].get_method() == "PUT"
    assert captured[0].data is None


async def test_missing_service_id_generates_unique_persistencia_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: list[Request] = []

    def fake_urlopen(request: Request, timeout: float) -> FakeResponse:
        captured.append(request)
        return FakeResponse()

    from app.core import consul

    monkeypatch.setattr(consul, "urlopen", fake_urlopen)
    registrar = ConsulRegistrar(consul_settings(service_id=None))

    await registrar.register()

    registration = json.loads(captured[0].data or b"{}")
    assert re.fullmatch(r"persistencia-[0-9a-f]{8}", registration["ID"])
