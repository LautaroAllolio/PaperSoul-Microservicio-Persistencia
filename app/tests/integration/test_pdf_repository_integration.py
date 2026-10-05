"""GATE G1 — `BeaniePdfRepository` contra MongoDB real.

Este es el único lugar del repo donde se comprueba que Mongo **hace** lo que el
modelo **declara**. Los tests unitarios del fake prueban la semántica que el código
expresa; estos prueban que el servidor la respeta. La diferencia importa porque casi
todo el contrato depende de decisiones del servidor:

- la unicidad de `pdf_hash` la impone el índice, no el código. Si Mongo no lo crea, dos
  subidas simultáneas del mismo PDF se guardan dos veces y no hay forma de detectarlo
  desde Python (SPEC §8.3, trampa nº4);
- el orden estable `uploaded_at DESC, _id DESC` depende del desempate por `_id`;
- el round-trip del offset depende de `tz_aware=True`.

Nada de esto se puede simular con confianza: un fake no sabe qué índices crea Mongo.
Por eso van marcados `integration` y sólo corren con Docker.

**No se ejecutan en local sin Docker Desktop.** El gate se cierra en CI (SC-20) o en
una máquina con Docker, no con `pytest` a secas.
"""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from bson import ObjectId
from motor.motor_asyncio import AsyncIOMotorClient

from app.exceptions.domain import (
    DuplicateResourceException,
    InvalidDocumentIdException,
    ResourceNotFoundException,
)
from app.models.pdf_document import ExtractionMethod, PdfDocument, PdfDocumentFields
from app.repositories.pdf_repository import BeaniePdfRepository

pytestmark = pytest.mark.integration


def fields_with(**overrides: object) -> PdfDocumentFields:
    """Campos válidos y mínimos, con `pdf_hash` único, sobrescribiendo lo que haga
    falta. Cada llamada da un `pdf_hash` distinto, así que dos tests no colisionan
    aunque compartieran base de datos.
    """
    values: dict[str, object] = {
        "pdf_hash": uuid4().hex,
        "filename": "contrato.pdf",
        "extracted_text": "texto extraído",
        "extraction_method": ExtractionMethod.PYMUPDF,
        "page_count": 3,
    }
    values.update(overrides)
    return PdfDocumentFields(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------- índices


async def test_mongo_creates_the_unique_index_on_pdf_hash(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """SC-03, y el gate G1 completo: el índice único existe **en el servidor**.

    Se consulta `index_information()` y no `PdfDocument.indexes()`: lo primero es lo
    que Mongo guarda de verdad. Un `unique: true` que sólo existe en el modelo es una
    intención, no una restricción.
    """
    collection = await PdfDocument.get_motor_collection()
    indexes = await collection.index_information()

    assert "uniq_pdf_hash" in indexes, f"no hay índice único de pdf_hash: {list(indexes)}"
    assert indexes["uniq_pdf_hash"]["unique"] is True


async def test_mongo_creates_the_descending_uploaded_at_index(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """El índice de listado no es una optimización menor: es lo que hace que la
    consulta ordene sin un `SORT` en memoria. Sin él, paginar deja de escalar.
    """
    collection = await PdfDocument.get_motor_collection()
    indexes = await collection.index_information()

    assert "idx_uploaded_at_id_desc" in indexes, f"no hay índice de listado: {list(indexes)}"
    key = indexes["idx_uploaded_at_id_desc"]["key"]
    assert list(key) == [("uploaded_at", -1), ("_id", -1)]


# ------------------------------------------------------------------- unicidad


async def test_creating_the_same_hash_twice_raises_a_domain_error(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """La traducción de `DuplicateKeyError` ocurre en la capa de datos.

    Si el `except` desapareciera, la excepción de pymongo escaparía hasta el handler
    genérico y el cliente recibiría un 500 por un conflicto que él sí puede evitar.
    """
    repository = BeaniePdfRepository()
    fields = fields_with()

    await repository.create(fields)

    with pytest.raises(DuplicateResourceException):
        await repository.create(fields)


async def test_two_different_hashes_coexist(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """Contraprueba del anterior: sin ella, un `except DuplicateKeyError` demasiado
    ancho (o un índice mal definido) haría fallar el caso normal.
    """
    repository = BeaniePdfRepository()

    first = await repository.create(fields_with())
    second = await repository.create(fields_with())

    assert first.id != second.id


# ------------------------------------------------------------------ lecturas


async def test_get_by_hash_returns_the_stored_document(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    repository = BeaniePdfRepository()
    fields = fields_with(filename="tesis.pdf")

    created = await repository.create(fields)
    found = await repository.get_by_hash(fields.pdf_hash)

    assert found is not None
    assert found.id == created.id
    assert found.filename == "tesis.pdf"


async def test_get_by_hash_returns_none_when_absent(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """No encontrar no es un error: es la respuesta normal de `GET /by-hash`."""
    repository = BeaniePdfRepository()

    assert await repository.get_by_hash(uuid4().hex) is None


async def test_get_by_id_rejects_a_malformed_id(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """SC-06: id mal formado da 400, no 500.

    El corte se hace *antes* de tocar la base de datos. Si se consultara primero y se
    tradujera el error del driver, un id con formato inválido sería indistinguible de
    un problema de conexión.
    """
    repository = BeaniePdfRepository()

    with pytest.raises(InvalidDocumentIdException):
        await repository.get_by_id("no-es-un-objectid")


async def test_get_by_id_raises_not_found_when_absent(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    repository = BeaniePdfRepository()

    with pytest.raises(ResourceNotFoundException):
        await repository.get_by_id(str(ObjectId()))


# ----------------------------------------------------------- orden y paginación


async def test_listing_orders_by_uploaded_at_descending(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """SC-05: primero `uploaded_at` descendente.

    Los `uploaded_at` se pasan explícitos para no depender del reloj ni del
    `default_factory` del modelo.
    """
    repository = BeaniePdfRepository()
    base = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    await repository.create(fields_with(uploaded_at=base))
    await repository.create(fields_with(uploaded_at=base + timedelta(hours=2)))
    await repository.create(fields_with(uploaded_at=base + timedelta(hours=1)))

    page = await repository.list_paginated(limit=10, offset=0)

    assert [item.uploaded_at for item in page.items] == [
        base + timedelta(hours=2),
        base + timedelta(hours=1),
        base,
    ]
    assert page.total == 3


async def test_documents_sharing_an_instant_keep_a_stable_order(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """El desempate por `_id` es lo que evita que la paginación repita o salte
    documentos. `uploaded_at` tiene granularidad de milisegundos, así que dos
    inserciones dentro del mismo tick empatan de verdad.

    El criterio no es "cualquier orden", sino **el mismo** orden en las dos lecturas:
    eso es lo que necesita un cliente que pagina.
    """
    repository = BeaniePdfRepository()
    instant = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    for _ in range(5):
        await repository.create(fields_with(uploaded_at=instant))

    first_reading = await repository.list_paginated(limit=5, offset=0)
    second_reading = await repository.list_paginated(limit=5, offset=0)

    first_ids = [item.id for item in first_reading.items]
    assert first_ids == [item.id for item in second_reading.items]
    assert first_ids == sorted(first_ids, reverse=True)


async def test_pagination_splits_without_gaps_or_repeats(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """Recorrer la colección en páginas de 2 no pierde ni duplica documentos.

    Es el caso de uso real de `limit`/`offset`: un cliente que sube 5 documentos y los
    va pidiendo por ventanas.
    """
    repository = BeaniePdfRepository()
    for _ in range(5):
        await repository.create(fields_with())

    first = await repository.list_paginated(limit=2, offset=0)
    second = await repository.list_paginated(limit=2, offset=2)
    third = await repository.list_paginated(limit=2, offset=4)

    seen = [item.id for item in first.items + second.items + third.items]

    assert len(seen) == 5
    assert len(set(seen)) == 5
    assert first.total == second.total == third.total == 5
    assert first.offset == 0
    assert third.offset == 4


async def test_listing_an_empty_collection_returns_an_empty_page(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """Un listado vacío es un 200 con `items: []`, no un 404 ni una excepción."""
    repository = BeaniePdfRepository()

    page = await repository.list_paginated(limit=10, offset=0)

    assert page.items == []
    assert page.total == 0


# -------------------------------------------------------------------- borrado


async def test_delete_removes_the_document(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    repository = BeaniePdfRepository()
    created = await repository.create(fields_with())

    await repository.delete(created.id)

    with pytest.raises(ResourceNotFoundException):
        await repository.get_by_id(created.id)


async def test_delete_rejects_a_malformed_id(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    repository = BeaniePdfRepository()

    with pytest.raises(InvalidDocumentIdException):
        await repository.delete("no-es-un-objectid")


# ---------------------------------------------------------------- fechas y tipos


async def test_uploaded_at_keeps_the_instant_through_a_round_trip(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """SC-07: el round-trip conserva el instante y lo devuelve con offset.

    Se guarda un instante con offset -03:00 y se espera +00:00 con la **misma** hora
    UTC. BSON guarda el instante como milisegundos desde epoch, así que el offset que
    se recupera es el del cliente (`tz_aware=True`), no el del servidor.

    Es el test que detecta un `tz_aware` olvidado: sin él, `uploaded_at` vuelve *naive*
    y la comparación falla con `can't compare offset-naive and offset-aware datetimes`.
    """
    repository = BeaniePdfRepository()
    local_time = datetime(2026, 3, 1, 9, 0, tzinfo=timedelta(hours=-3))

    created = await repository.create(fields_with(uploaded_at=local_time))
    found = await repository.get_by_id(created.id)

    assert found is not None
    assert found.uploaded_at.tzinfo is not None
    assert found.uploaded_at == local_time.astimezone(UTC)


async def test_stored_document_projects_the_id_as_a_string(
    mongo_client: tuple[AsyncIOMotorClient, str],
) -> None:
    """El contrato devuelve `id: str`. Si se colgara un `ObjectId` en el tipo de
    retorno, Pydantic lo serializaría como `{"$oid": ...}` y el cliente recibiría un
    objeto donde espera texto.
    """
    repository = BeaniePdfRepository()

    created = await repository.create(fields_with())

    assert isinstance(created.id, str)
    assert len(created.id) == 24
