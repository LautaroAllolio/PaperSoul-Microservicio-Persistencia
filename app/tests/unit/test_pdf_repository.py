"""Tests del contrato del repositorio y su fake (lote S3, tarea 3.2).

Capa 3. El servicio habla con este contrato, nunca con Mongo directamente.

Estos tests **no requieren Mongo**: verifican el contrato y su semántica, no que
Mongo obedezca. Eso lo comprueba `tests/integration/` contra una instancia real.
La separación es deliberada (§8.1): si toda la suite dependiera de Docker,
dejaría de correr en cada commit, que es cuando un test vale algo.

`FakePdfRepository` es una implementación de primera clase del mismo contrato,
escrita a mano y no con `unittest.mock` (§8.2): un `Mock` que devuelve lo que el
test espera pasa aunque el código real esté roto; un fake con la unicidad
implementada de verdad puede fallar.
"""

from datetime import UTC, datetime, timedelta

import pytest

from app.exceptions.domain import (
    DuplicateResourceException,
    InvalidDocumentIdException,
    ResourceNotFoundException,
)
from app.models.pdf_document import PdfDocumentFields, StoredDocument
from app.repositories.pdf_repository import PdfRepository
from app.tests.fakes import FakePdfRepository

HASH_A = "a" * 64
HASH_B = "b" * 64

# Formato de un ObjectId de Mongo: 24 hex. Los ids del fake tienen esta forma
# para que el servicio no pueda distinguir uno de otro por la longitud.
OBJECT_ID_LIKE = "507f1f77bcf86cd799439011"
MISSING_OBJECT_ID = "507f1f77bcf86cd799439099"


def make_fields(pdf_hash: str = HASH_A, **overrides: object) -> PdfDocumentFields:
    """Campos de un documento válido, para poder construirlo sin base de datos."""
    fields: dict[str, object] = {
        "filename": f"{pdf_hash[:8]}.pdf",
        "extracted_text": "texto",
        "extraction_method": "pymupdf",
        "page_count": 1,
        "pdf_hash": pdf_hash,
    }
    fields.update(overrides)
    return PdfDocumentFields(**fields)


def utc(year: int, month: int, day: int) -> datetime:
    """Fecha UTC explícita. Se evita `datetime.now()` con offsets relativos porque
    hace los tests dependientes del instante de ejecución.
    """
    return datetime(year, month, day, tzinfo=UTC)


async def test_fake_implements_the_repository_contract() -> None:
    """El fake implementa el mismo contrato que la implementación real.

    Si no lo hiciera, los tests del servicio pasarían contra un doble que se
    comporta de otra manera, y el bug aparecería sólo contra Mongo.
    """
    assert isinstance(FakePdfRepository(), PdfRepository)


async def test_created_document_can_be_read_back_by_hash() -> None:
    """US-1: el caso de uso principal del servicio."""
    repository = FakePdfRepository()
    created = await repository.create(make_fields(HASH_A))

    found = await repository.get_by_hash(HASH_A)

    assert found == created
    assert found.pdf_hash == HASH_A


async def test_unknown_hash_returns_none_rather_than_raising() -> None:
    """No encontrar no es un error: es la respuesta normal de `by-hash`, que
    responde 200 con `exists: false`. Lanzar aquí obligaría al servicio a
    distinguir dos casos que el endpoint trata igual.
    """
    assert await FakePdfRepository().get_by_hash(HASH_A) is None


async def test_get_by_hash_is_case_sensitive() -> None:
    """D-1: los hashes son minúsculas siempre. Si el lookup no distinguiera mayúsculas,
    un productor que mandara el hash en mayúsculas encontraría un documento en
    lugar de recibir un 422 que le diga que su bug es real.
    """
    repository = FakePdfRepository()
    await repository.create(make_fields(HASH_A))

    assert await repository.get_by_hash(HASH_A.upper()) is None


async def test_creating_the_same_hash_twice_raises_duplicate() -> None:
    """US-2: el `pdf_hash` repetido se traduce a 409.

    La unicidad la impone la base de datos, no un `if` en el servicio: entre dos
    peticiones concurrentes, un `get_by_hash` seguido de `create` deja pasar las
    dos, y la deduplicación se rompe justo cuando más importa.
    """
    repository = FakePdfRepository()
    await repository.create(make_fields(HASH_A))

    with pytest.raises(DuplicateResourceException):
        await repository.create(make_fields(HASH_A))


async def test_duplicates_are_detected_by_hash_not_by_filename() -> None:
    """Dos archivos distintos con el mismo nombre no chocan: el nombre no es clave
    de unicidad y confundirlo rechazaría subidas legítimas.
    """
    repository = FakePdfRepository()
    await repository.create(make_fields(HASH_A, filename="contrato.pdf"))

    await repository.create(make_fields(HASH_B, filename="contrato.pdf"))

    assert repository.count() == 2


async def test_get_by_id_returns_the_stored_document() -> None:
    repository = FakePdfRepository()
    created = await repository.create(make_fields(HASH_A))

    assert await repository.get_by_id(created.id) == created


async def test_get_by_id_on_a_valid_but_missing_id_raises_not_found() -> None:
    """SC-06: ObjectId válido e inexistente es 404. El servicio traduce esta
    excepción; el repositorio sólo señala que no está.
    """
    with pytest.raises(ResourceNotFoundException):
        await FakePdfRepository().get_by_id(MISSING_OBJECT_ID)


async def test_get_by_id_on_a_malformed_id_raises_invalid_id() -> None:
    """SC-06: un id que no es ObjectId es 400, y se detecta **antes** de tocar
    Mongo. Consultar con un id inválido convierte un error del cliente en un 500.
    """
    with pytest.raises(InvalidDocumentIdException):
        await FakePdfRepository().get_by_id("no-es-un-objectid")


async def test_listing_returns_newest_first() -> None:
    """US-3: orden `uploaded_at` descendente."""
    repository = FakePdfRepository()
    await repository.create(make_fields(HASH_A, uploaded_at=utc(2026, 1, 1)))
    await repository.create(make_fields(HASH_B, uploaded_at=utc(2026, 3, 1)))

    page = await repository.list_paginated(limit=10, offset=0)

    hashes = [item.pdf_hash for item in page.items]

    assert hashes == [HASH_B, HASH_A]


async def test_listing_breaks_uploaded_at_ties_by_id_descending() -> None:
    """SC-05, el criterio exacto: con `uploaded_at` repetido, `_id` desempata sin
    repetir ni perder ítems entre páginas.

    Es el caso que vuelve inútil un orden por `uploaded_at` a secas: dos documentos
    con el mismo milisegundo empatan, y el orden puede cambiar entre dos peticiones
    idénticas, con lo que un cliente que pagina ve documentos repetidos o saltados.
    """
    repository = FakePdfRepository()
    same_moment = utc(2026, 2, 2)
    first = await repository.create(make_fields(HASH_A, uploaded_at=same_moment))
    second = await repository.create(make_fields(HASH_B, uploaded_at=same_moment))

    page = await repository.list_paginated(limit=10, offset=0)

    items = page.items

    assert [item.id for item in items] == [second.id, first.id]


async def test_pagination_returns_the_requested_window() -> None:
    repository = FakePdfRepository()
    for index in range(5):
        await repository.create(make_fields(pdf_hash=f"{index:064d}"))

    page = await repository.list_paginated(limit=2, offset=2)

    assert len(page.items) == 2
    assert page.total == 5
    assert page.limit == 2
    assert page.offset == 2


async def test_windows_of_pagination_do_not_overlap_or_skip() -> None:
    """SC-05 sobre el criterio observable: recorrer la colección por páginas de 2
    entrega cada documento exactamente una vez y en el mismo orden.

    Es la propiedad que un `sort` no determinista rompe, y la que un test de una
    sola página no detectaría.
    """
    repository = FakePdfRepository()
    for index in range(6):
        await repository.create(make_fields(pdf_hash=f"{index:064d}", uploaded_at=utc(2026, 1, 1)))

    collected: list[str] = []
    for offset in (0, 2, 4):
        page = await repository.list_paginated(limit=2, offset=offset)
        collected += [item.id for item in page.items]

    assert len(collected) == len(set(collected)) == 6


async def test_total_counts_the_whole_collection_not_the_window() -> None:
    """`total` es el total de la colección, no el tamaño de la página.

    Confundirlo hace que un cliente que pagina por `offset` se salte el final: vería
    la última página con `total` igual al tamaño de la ventana y concluiría que ya
    no hay más.
    """
    repository = FakePdfRepository()
    for index in range(5):
        await repository.create(make_fields(pdf_hash=f"{index:064d}"))

    page = await repository.list_paginated(limit=2, offset=4)

    assert page.total == 5
    assert len(page.items) == 1


async def test_offset_past_the_end_returns_an_empty_page_rather_than_raising() -> None:
    """Pedir la página 99 de una colección de 1 documento devuelve vacío con el
    `total` correcto. Un error obligaría al cliente a manejar un caso más.
    """
    repository = FakePdfRepository()
    await repository.create(make_fields(HASH_A))

    page = await repository.list_paginated(limit=20, offset=100)

    assert page.items == []
    assert page.total == 1


async def test_delete_removes_the_document() -> None:
    """US-4."""
    repository = FakePdfRepository()
    created = await repository.create(make_fields(HASH_A))

    await repository.delete(created.id)

    assert await repository.get_by_hash(HASH_A) is None


async def test_deleting_twice_raises_not_found() -> None:
    """SC-08: la segunda llamada al `DELETE` da 404, no un 204 idempotente.

    Es una decisión, no un descuido: 204 en la segunda llamada haría que un
    cliente que reintenta por timeout no pudiera distinguir "borrado por mí" de
    "borrado por otro".
    """
    repository = FakePdfRepository()
    created = await repository.create(make_fields(HASH_A))
    await repository.delete(created.id)

    with pytest.raises(ResourceNotFoundException):
        await repository.delete(created.id)


async def test_deleting_a_malformed_id_raises_invalid_id() -> None:
    with pytest.raises(InvalidDocumentIdException):
        await FakePdfRepository().delete("no-es-un-objectid")


async def test_hash_becomes_reusable_after_deletion() -> None:
    """El índice único sigue existiendo tras un borrado, así que el mismo PDF se
    puede volver a subir. Si el repositorio no lo permitiera, un cliente que
    reintenta una subida fallida se quedaría bloqueado para siempre.
    """
    repository = FakePdfRepository()
    created = await repository.create(make_fields(HASH_A))
    await repository.delete(created.id)

    recreated = await repository.create(make_fields(HASH_A))

    assert recreated.id != created.id


async def test_stored_document_carries_the_id_and_the_domain_fields() -> None:
    """`StoredDocument` es lo que devuelve el repositorio: los campos de dominio más
    el id. Se comprueba que un id con formato de ObjectId es aceptable, que es lo
    que el repositorio real devuelve.
    """
    stored = StoredDocument(id=OBJECT_ID_LIKE, **make_fields(HASH_A).model_dump())

    assert stored.id == OBJECT_ID_LIKE
    assert stored.pdf_hash == HASH_A


async def test_fake_ids_look_like_object_ids() -> None:
    """El fake genera ids con la forma de un ObjectId para que el servicio no
    pueda pasar tests con ids de una forma que el backend real no produce.
    """
    created = await FakePdfRepository().create(make_fields(HASH_A))

    assert len(created.id) == 24
    assert int(created.id, 16) >= 0


async def test_fake_ids_increase_monotonically() -> None:
    """Como los ObjectId reales, los ids del fake ordenan por antigüedad. El test de
    desempate de `uploaded_at` depende de ello, así que se fija explícitamente en
    vez de dejarlo como una casualidad de la implementación.
    """
    repository = FakePdfRepository()
    ids = []
    for index in range(3):
        created = await repository.create(make_fields(f"{index:064d}"))
        ids.append(created.id)

    assert ids == sorted(ids)


async def test_timestamps_survive_the_round_trip() -> None:
    """D-2 y SC-07: lo que sale del repositorio conserva el offset UTC. Un naive en
    este punto se convertiría en hora local al serializar, y el mismo documento
    respondería distinto según dónde corra el servicio.
    """
    repository = FakePdfRepository()
    moment = utc(2026, 7, 1)
    created = await repository.create(make_fields(HASH_A, uploaded_at=moment))

    found = await repository.get_by_hash(HASH_A)
    assert found is not None
    assert found.uploaded_at.utcoffset() == timedelta(0)
    assert created.uploaded_at == moment
