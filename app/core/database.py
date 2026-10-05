"""Cliente de MongoDB y arranque de Beanie.

**Este archivo, junto con `repositories/pdf_repository.py`, es la frontera de
escape** de la migración a pymongo (SPEC.md §Escapatoria). Al cambiarla, sólo
`app/services/` y `app/api/` deben seguir igual; y no van a verse obligadas a
cambiar, porque aquí no se expone ningún tipo del driver más allá de estas
funciones.

`init_database` recibe un cliente ya creado en vez de crear uno. La razón es
concreta: si esta función fabricara el cliente y fallara después (por ejemplo al
registrar los modelos), el cliente ya creado se quedaría huérfano y quien lo
llamara no tendría referencia para cerrarlo. Quien crea un recurso es quien debe
cerrarlo; el lifespan lo cumple y esta función sólo configura.
"""

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.models.pdf_document import PdfDocument

logger = logging.getLogger(__name__)

ClientFactory = Callable[..., Any]
InitBeanie = Callable[..., Awaitable[Any]]


def create_client(uri: str) -> AsyncIOMotorClient:
    """Crea el cliente de Mongo.

    `tz_aware=True` es obligatorio y no una preferencia (D-2, SC-07). El default
    de pymongo es `False`, y con él `uploaded_at` vuelve de la base de datos como
    `datetime` **naive**: la respuesta saldría sin offset y el mismo documento se
    leería de forma distinta según la zona horaria de quien lo consulta.

    `serverSelectionTimeoutMS` se deja en el default de pymongo a propósito. Bajarlo
    haría que un arranque con la base de datos aún no lista fallara con un error
    genérico en lugar de esperar a que Mongo esté disponible, que es justo lo que
    necesita un despliegue en contenedor.
    """
    return AsyncIOMotorClient(uri, tz_aware=True)


async def init_database(
    client: AsyncIOMotorClient,
    database_name: str,
    init_beanie_func: InitBeanie = init_beanie,
) -> AsyncIOMotorDatabase:
    """Registra los modelos de Beanie sobre un cliente existente y devuelve la base de datos.

    `PdfDocument` tiene que estar en la lista: sin él, Beanie inicializa la colección
    vacía y cualquier consulta posterior falla con `CollectionWasNotInitialized`,
    que es la trampa nº2 de la SPEC (§8.3).

    `init_beanie_func` es inyectable para que este arranque se pueda probar sin
    Mongo. El parámetro no lo usa producción, pero es lo que permite que el
    lifespan se testee en el gate de cada commit en lugar de sólo cuando alguien
    tiene Docker.
    """
    database = client[database_name]
    await init_beanie_func(database=database, document_models=[PdfDocument])
    logger.info("Conectado a MongoDB, base de datos %r", database_name)
    return database


async def close_database(client: AsyncIOMotorClient) -> None:
    """Cierra el cliente y devuelve sus sockets al sistema.

    Sin esto, un proceso que abre y cierra conexiones en cada test (o el
    orquestador de contenedores reiniciando el proceso) agota los descriptores de
    fichero disponibles.
    """
    client.close()
    logger.info("Cliente de MongoDB cerrado")
