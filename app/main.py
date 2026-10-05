"""Punto de entrada de la aplicación.

Dos funciones, y la separación es deliberada:

- `create_app(...)` construye el objeto `FastAPI` sin abrir ninguna conexión.
- `lifespan(...)` es el que conecta y desconecta Mongo, y se ejecuta al arrancar
  y apagar el servidor.

`create_app` recibe el repositorio, así que una app entera se monta con un fake y
sin Docker. Es la condición para que los tests de API existan antes de que haya una
base de datos.
"""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from beanie import init_beanie
from fastapi import FastAPI

from app.api.errors import register_exception_handlers
from app.api.health import router as health_router
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.container import Container, build_container
from app.core.database import (
    ClientFactory,
    InitBeanie,
    close_database,
    create_client,
    init_database,
)
from app.core.logging import configure_logging
from app.repositories.pdf_repository import PdfRepository

logger = logging.getLogger(__name__)

API_V1_PREFIX = "/api/v1"


def create_app(
    repository: PdfRepository | None = None,
    container: Container | None = None,
    client_factory: ClientFactory = create_client,
    init_beanie_func: InitBeanie = init_beanie,
) -> FastAPI:
    """Construye la aplicación.

    `repository` o `container` con, se construye el contenedor. Sin ninguno de los
    dos se usa la implementación real, que es el caso de producción.

    `client_factory` e `init_beanie_func` son puntos de inyección para poder
    levantar la aplicación entera en un test sin Mongo.

    El `Container` se publica en `app.state` **aquí** y no en el lifespan, y la
    distinción es deliberada: el contenedor es el grafo de dependencias, que no
    necesita conexión, y el lifespan sólo gestiona el recurso (el cliente de Mongo).
    Publicarlo en el lifespan ataría los routers a que el arranque se ejecutara, y
    `ASGITransport` no lo dispara: los tests de API no podrían ni arrancar.
    """
    settings = get_settings()
    configure_logging(settings.log_level)

    if container is None:
        if repository is None:
            # Producción: el repositorio real necesita la colección de Beanie
            # inicializada, y eso ocurre en el lifespan. Se construye aquí
            # igualmente porque abrir la conexión no es cosa de este constructor.
            from app.repositories.pdf_repository import BeaniePdfRepository

            repository = BeaniePdfRepository()
        container = build_container(repository)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Conecta al arrancar y desconecta al apagar, incluso si algo falla.

        El cliente se crea **antes** del `try`, no dentro. Si se creara dentro y
        `init_database` fallara, la asignación a `client` no llegaría a completarse,
        el `finally` vería `None` y no cerraría nada: el proceso se quedaría con la
        conexión abierta y cada reintento del orquestador agotaría un descriptor.

        Por eso el cliente se pide antes de todo lo que puede fallar. Es la misma
        regla que en cualquier gestión de recursos: se adquiere fuera del bloque
        que garantiza la liberación.
        """
        client = client_factory(settings.mongodb_uri)
        try:
            database = await init_database(
                client=client,
                database_name=settings.mongodb_database,
                init_beanie_func=init_beanie_func,
            )
            app.state.mongo_client = client
            app.state.mongo_database = database
            yield
        finally:
            await close_database(client)
            app.state.mongo_client = None
            app.state.mongo_database = None

    app = FastAPI(
        title=settings.app_title,
        version=settings.app_version,
        lifespan=lifespan,
    )
    # Antes del lifespan a propósito: los routers resuelven el servicio por `Depends`
    # al atender la primera petición, y para entonces el contenedor ya está aquí.
    app.state.container = container
    app.include_router(health_router)
    app.include_router(api_router, prefix=API_V1_PREFIX)
    register_exception_handlers(app)
    return app


# Instancia para `uvicorn app.main:app`. Se construye a nivel de módulo porque el
# servidor ASGI espera un objeto, no una fábrica; la vida útil de la conexión la
# gestiona el lifespan de arriba, no este `app`.
app = create_app()
