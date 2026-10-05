"""Tests del arranque de la aplicación (lote S3, tarea 3.4).

Verifican dos cosas que en producción se confunden y en los tests no:

1. El lifespan **conecta y desconecta** Mongo, y lo hace incluso si el arranque
   falla a medias. Un `close_database` colocado fuera del `finally` deja el cliente
   abierto y cada reintento del orquestador agota un descriptor de fichero.
2. El arranque **es inyectable**: `create_app` recibe el repositorio, así que un
   test monta la aplicación entera con un fake y sin Docker. Ésta es la condición
   para que los tests de API de S4 existan.

Ningún test abre un socket: los dobles implementan exactamente los dos atributos
que el arranque toca, `cliente[nombre]` y `close()`.
"""

from typing import Any

import httpx
import pytest
from asgi_lifespan import LifespanManager
from fastapi import FastAPI

from app.core.container import build_container
from app.core.database import ClientFactory
from app.main import create_app
from app.repositories.pdf_repository import PdfRepository
from app.tests.fakes import FakePdfRepository


class FakeDatabase:
    """Sustituto de `AsyncIOMotorDatabase`. Sólo se le pasa a `init_beanie`."""

    def __init__(self, name: str) -> None:
        self.name = name


class FakeClient:
    """Doble del cliente de Motor. Registra lo que se le pide, no lo ejecuta.

    No implementa la interfaz real de Motor a propósito: el arranque sólo necesita
    que el cliente sea subscriptable y que sepa cerrarse, y un doble completo sería
    más código que el código que sustituye.
    """

    def __init__(self, fail_on_access: bool = False) -> None:
        self.closed = False
        self.accessed: list[str] = []
        self._fail_on_access = fail_on_access

    def __getitem__(self, database_name: str) -> FakeDatabase:
        if self._fail_on_access:
            raise ConnectionError("no hay Mongo")
        self.accessed.append(database_name)
        return FakeDatabase(database_name)

    def close(self) -> None:
        self.closed = True


class RecordingInitBeanie:
    """Sustituto de `init_beanie` que anota qué modelos se le piden registrar."""

    def __init__(self) -> None:
        self.document_models: list[Any] = []
        self.database_name: str | None = None

    async def __call__(self, database: FakeDatabase, document_models: list[Any]) -> None:
        self.document_models = document_models
        self.database_name = database.name


def returning(client: FakeClient) -> ClientFactory:
    """Fábrica de clientes que siempre devuelve el mismo doble.

    Se escribe como función en vez de repetir `lambda *a, **k: client` en cinco
    sitios: evita duplicar la firma y evita el `ARG005` de ruff sobre argumentos que
    la lambda no usa. El doble ya registra por su cuenta lo que se le pide.
    """

    def factory(*_args: Any, **_kwargs: Any) -> FakeClient:
        return client

    return factory


# --------------------------------------------------------------- core/database.py


def test_create_client_asks_motor_for_timezone_aware_datetimes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """SC-07 en el punto de origen: el cliente se crea con `tz_aware=True`.

    El default de pymongo es `False`, y con él `uploaded_at` vuelve de la base de
    datos como `datetime` **naive**: la respuesta saldría sin offset y el mismo
    documento se leería de forma distinta según la zona horaria de quien consulta.
    """
    from app.core import database

    captured: dict[str, Any] = {}

    def fake_constructor(uri: str, **kwargs: Any) -> FakeClient:
        captured["uri"] = uri
        captured.update(kwargs)
        return FakeClient()

    monkeypatch.setattr(database, "AsyncIOMotorClient", fake_constructor)

    database.create_client("mongodb://localhost:27017")

    assert captured["tz_aware"] is True
    assert captured["uri"] == "mongodb://localhost:27017"


async def test_init_database_selects_the_requested_database() -> None:
    """El nombre de la base de datos viene de la configuración, y equivocarse en él
    crea una colección vacía en la base incorrecta: la API responde 200 a un listado
    perpetuamente vacío y no hay ningún error visible.
    """
    from app.core.database import init_database

    client = FakeClient()
    init = RecordingInitBeanie()

    await init_database(client=client, database_name="papersoul", init_beanie_func=init)  # type: ignore[arg-type]

    assert client.accessed == ["papersoul"]
    assert init.database_name == "papersoul"


async def test_init_database_registers_the_document_model() -> None:
    """Sin `PdfDocument` en la lista de `init_beanie`, cualquier consulta posterior
    falla con `CollectionWasNotInitialized`. Es la trampa número 2 de la SPEC.
    """
    from app.core.database import init_database
    from app.models.pdf_document import PdfDocument

    init = RecordingInitBeanie()

    await init_database(
        client=FakeClient(),
        database_name="papersoul",
        init_beanie_func=init,
    )

    assert init.document_models == [PdfDocument]


async def test_close_database_closes_the_client() -> None:
    """Cerrar el cliente es lo que devuelve los sockets al sistema. Sin esto, un
    proceso que abre y cierra conexiones en cada test no termina nunca.
    """
    from app.core.database import close_database

    client = FakeClient()
    await close_database(client)  # type: ignore[arg-type]

    assert client.closed is True


# ------------------------------------------------------------- core/container.py


def test_build_container_accepts_an_injected_repository() -> None:
    """La inyección es lo que permite montar la app entera sin Mongo. Sin este
    parámetro, ningún test de API podría existir antes de tener Docker.
    """
    fake = FakePdfRepository()

    container = build_container(repository=fake)

    assert container.document_service._repository is fake


def test_build_container_wires_the_service_to_the_contract() -> None:
    """El contenedor entrega un servicio cuyo repositorio cumple el contrato, no
    una implementación concreta. Comprobarlo aquí evita descubrir en S4 que el
    router depende de una clase y no de una interfaz.
    """
    container = build_container(repository=FakePdfRepository())

    assert isinstance(container.document_service._repository, PdfRepository)


# --------------------------------------------------------------------- main.py


async def test_app_serves_requests_without_touching_mongo() -> None:
    """La app construida con `create_app` responde sin lifespan y sin base de datos.

    `httpx.ASGITransport` no dispara el `lifespan`. Que la app sirva sin él es lo que
    permite probar los endpoints con un fake: el lifespan es para producción, no un
    requisito para construir el objeto.
    """
    app = create_app(repository=FakePdfRepository())

    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        response = await client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


async def test_lifespan_closes_the_database_on_shutdown() -> None:
    """Tras el apagado, el cliente queda cerrado.

    Al salir del `async with` se ejecuta el `finally` del lifespan, así que el cierre
    es observable desde aquí.
    """
    client = FakeClient()
    app = create_app(
        repository=FakePdfRepository(),
        client_factory=returning(client),
        init_beanie_func=RecordingInitBeanie(),
    )

    async with LifespanManager(app):
        assert client.closed is False

    assert client.closed is True


async def test_lifespan_opens_the_connection_before_serving() -> None:
    """El cliente se abre **antes** de aceptar tráfico.

    Un servicio que acepta peticiones y sólo entonces intenta conectar a Mongo
    devuelve errores a los primeros clientes reales durante el arranque.
    """
    client = FakeClient()
    app = create_app(
        repository=FakePdfRepository(),
        client_factory=returning(client),
        init_beanie_func=RecordingInitBeanie(),
    )

    async with LifespanManager(app):
        assert client.accessed == ["papersoul"]


async def test_lifespan_publishes_the_container_on_app_state() -> None:
    """El contenedor queda en `app.state` para que `Depends` lo encuentre. Sin esto,
    los routers no tienen forma de obtener el servicio.
    """
    app = create_app(
        repository=FakePdfRepository(),
        client_factory=returning(FakeClient()),
        init_beanie_func=RecordingInitBeanie(),
    )

    async with LifespanManager(app):
        assert app.state.container is not None


async def test_lifespan_closes_the_database_even_when_startup_fails() -> None:
    """Si no se puede conectar, el cliente se cierra igualmente.

    Sin esto, un despliegue con la base de datos inaccesible agotaría descriptores
    de fichero en cada reintento del orquestador de contenedores. Es el motivo por
    el que el cliente se pide **fuera** del `try`: si se creara dentro, la asignación
    no se completaría y el `finally` vería `None`.
    """
    client = FakeClient(fail_on_access=True)
    app = create_app(
        repository=FakePdfRepository(),
        client_factory=returning(client),
        init_beanie_func=RecordingInitBeanie(),
    )

    with pytest.raises(ConnectionError):
        async with LifespanManager(app):
            pass

    assert client.closed is True


def test_module_level_app_exists_for_the_asgi_server() -> None:
    """`uvicorn app.main:app` espera un objeto, no una fábrica. Sin este atributo,
    el Dockerfile del proyecto no arrancaría nada.
    """
    from app.main import app as module_app

    assert isinstance(module_app, FastAPI)
