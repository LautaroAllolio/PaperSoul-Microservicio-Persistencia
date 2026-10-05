"""Fixtures de integración contra MongoDB real (vía Testcontainers).

Viven aquí, y no en `app/tests/conftest.py`, a propósito: el conftest raíz se importa
en **cada** test, y en environments sin Docker (la mayoría, incluidos los alumnos)
importar `testcontainers` sin necesidad sólo añade coste y una dependencia que
puede faltar. Estas fixtures sólo se usan desde `integration/`.

Las tres trampas de SPEC §8.3 están resueltas aquí, y son la razón de que este
fichero sea más explícito de lo que parece necesario:

1. `mongo_uri` es **session-scoped y devuelve un `str`**, nunca un driver. Un driver
   de nivel de sesión sobrevive al cierre del event loop de pytest-asyncio y acaba
   en `RuntimeError: Task attached to a different loop` o en
   `Event loop is closed` al final de la sesión. El contenedor se puede compartir;
   el driver no.
2. `mongo_client` es **function-scoped**, así que el loop de cada test es el suyo y
   su cliente se cierra con él.
3. `init_beanie` se llama en la fixture, no en los tests. Sin él, cualquier acceso a
   `PdfDocument` lanza `CollectionWasNotInitialized`, y el error aparece en un test
   que no habla de inicialización, que es la peor forma de falla.
"""

from collections.abc import AsyncIterator, Iterator
from uuid import uuid4

import pytest
from beanie import init_beanie
from motor.motor_asyncio import AsyncIOMotorClient
from testcontainers.community.mongodb import MongoDbContainer

from app.models.pdf_document import PdfDocument


@pytest.fixture(scope="session")
def mongo_uri() -> Iterator[str]:
    """Levanta Mongo una vez para toda la sesión y devuelve **su URI como texto**.

    Que sea texto y no un driver es la trampa nº1, y la razón está en el `yield`:
    devolver el driver aquí es tentador (evita parsear la URI en cada test) y es
    exactamente lo que rompe la suite al final de la sesión.

    La imagen va fijada a `mongo:7` y no a `latest`: un `latest` que cambia de
    versión puede cambiar el nombre o la forma del índice y hacer fallar G1 por un
    cambio ajeno a este repo.
    """
    with MongoDbContainer("mongo:7") as mongo:
        yield mongo.get_connection_url()


@pytest.fixture
async def mongo_client(mongo_uri: str) -> AsyncIterator[tuple[AsyncIOMotorClient, str]]:
    """Cliente limpio y Beanie inicializado, uno por test.

    Cada test usa una base de datos con nombre único (`papersoul_test_<uuid>`), de
    modo que los tests no se ven entre sí y un fallo no deja datos poisons para el
    siguiente. La base se borra al terminar: el contenedor es de sesión, pero la
    aislación es de función.

    `tz_aware=True` es el mismo motivo que en producción (SC-07): sin él, `uploaded_at`
    vuelve *naive* y el test de round-trip del offset pasa en local y falla en CI.
    """
    client = AsyncIOMotorClient(mongo_uri, tz_aware=True)
    database_name = f"papersoul_test_{uuid4().hex[:8]}"
    try:
        await init_beanie(database=client[database_name], document_models=[PdfDocument])
        yield client, database_name
    finally:
        await client.drop_database(database_name)
        client.close()
