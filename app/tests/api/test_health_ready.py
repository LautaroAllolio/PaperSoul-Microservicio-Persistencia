"""Comportamiento HTTP de las sondas de salud y readiness."""

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from httpx import ASGITransport

from app.main import create_app
from app.tests.fakes import FakePdfRepository


@pytest.fixture
def ready_client() -> tuple[httpx.AsyncClient, AsyncMock]:
    """Cliente HTTP con un ping de Mongo sustituible en el límite externo."""
    ping = AsyncMock(return_value={"ok": 1})
    app = create_app(repository=FakePdfRepository())
    app.state.mongo_client = SimpleNamespace(admin=SimpleNamespace(command=ping))
    client = httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )
    return client, ping


async def test_ready_returns_200_when_mongo_ping_succeeds(
    ready_client: tuple[httpx.AsyncClient, AsyncMock],
) -> None:
    client, ping = ready_client
    async with client:
        response = await client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {"status": "ready", "database": "connected"}
    ping.assert_awaited_once_with("ping")


async def test_ready_returns_sanitized_problem_when_mongo_is_unavailable(
    ready_client: tuple[httpx.AsyncClient, AsyncMock],
) -> None:
    client, ping = ready_client
    ping.side_effect = RuntimeError("mongodb://user:secret@internal-db:27017")

    async with client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == "urn:problem:papersoul:database-unavailable"
    assert response.json()["detail"] == "La base de datos no está disponible."
    assert "secret" not in response.text
    assert "internal-db" not in response.text


async def test_ready_returns_503_when_mongo_ping_exceeds_500ms(
    ready_client: tuple[httpx.AsyncClient, AsyncMock],
) -> None:
    client, ping = ready_client

    async def slow_ping(command: str) -> dict[str, int]:
        await asyncio.sleep(0.6)
        return {"ok": 1}

    ping.side_effect = slow_ping

    async with client:
        response = await client.get("/ready")

    assert response.status_code == 503
    assert response.json()["type"] == "urn:problem:papersoul:database-unavailable"


async def test_health_remains_live_without_a_mongo_client() -> None:
    app = create_app(repository=FakePdfRepository())
    client = httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://testserver",
    )

    async with client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}
