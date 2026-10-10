"""Tests del servicio de documentos (lote S3, tarea 3.3).

Capa 2. Se prueba con `FakePdfRepository`, sin Mongo (§8.1). Lo que se verifica
aquí es lógica de negocio pura; que el índice único de Mongo realmente rechace un
duplicado es cosa de `tests/integration/`.

Los casos de uso verificados:

- **US-1** `find_by_hash` responde siempre con datos, exista o no el documento.
- **US-2** `create` traduce un duplicado a un conflicto que el handler sirve 409.
- **US-3** `list_documents` pagina con el orden estable del contrato.
- **US-4** `delete` distingue "borrado" de "no existía" y de "id inválido".

Todos los tests usan el mismo fake que la implementación real cumple, así que un
cambio en el contrato los rompe a la vez y no uno por uno.
"""

from datetime import UTC, datetime

import pytest

from app.exceptions.domain import (
    DuplicateResourceException,
    InvalidDocumentIdException,
    ResourceNotFoundException,
)
from app.services.document_service import DocumentService
from app.tests.fakes import FakePdfRepository

HASH_A = "a" * 64
HASH_B = "b" * 64
HASH_C = "c" * 64

VALID_OBJECT_ID = "507f1f77bcf86cd799439011"
MISSING_OBJECT_ID = "507f1f77bcf86cd799439099"
MALFORMED_OBJECT_ID = "no-es-un-objectid"


def make_request(pdf_hash: str = HASH_A, **overrides: object):
    """Payload de creación válido, del mismo tipo que el router recibirá."""
    from app.schemas.document import DocumentCreateRequest

    fields: dict[str, object] = {
        "filename": f"{pdf_hash[:8]}.pdf",
        "extracted_text": "texto extraído",
        "extraction_method": "pymupdf",
        "page_count": 2,
        "pdf_hash": pdf_hash,
    }
    fields.update(overrides)
    return DocumentCreateRequest(**fields)


@pytest.fixture
def repository() -> FakePdfRepository:
    return FakePdfRepository()


@pytest.fixture
def service(repository: FakePdfRepository) -> DocumentService:
    """Servicio con el fake inyectado.

    El servicio recibe el repositorio por constructor, no lo busca en un singleton.
    Es lo que permite probarlo sin contenedor de dependencias y sin base de datos.
    """
    return DocumentService(repository)


# --------------------------------------------------------------------------- US-1


async def test_find_by_hash_reports_existence_when_the_document_exists(
    service: DocumentService,
) -> None:
    created = await service.create(make_request(HASH_A))

    result = await service.find_by_hash(HASH_A)

    assert result.exists is True
    assert result.id == created.id
    assert result.uploaded_at == created.uploaded_at


async def test_find_by_hash_reports_absence_without_raising(service: DocumentService) -> None:
    """US-1: un hash desconocido NO es un error. `GET /by-hash/{pdf_hash}` responde
    200 con `exists: false` pase lo que pase, y distinguishable del 404 que sí
    devuelve `GET /{id}`.

    Si esto lanzara, el orquestador tendría que capturar excepciones para una
    consulta que es de su flujo normal: preguntará si un PDF ya está subido.
    """
    result = await service.find_by_hash(HASH_A)

    assert result.exists is False
    assert result.id is None
    assert result.uploaded_at is None


async def test_find_by_hash_is_case_sensitive(service: DocumentService) -> None:
    """D-1: los hashes son minúsculas. Una consulta en mayúsculas debe llegar al
    camino de "no existe", no encontrar el documento.

    Normalizar aquí convertiría un bug del productor en un acierto silencioso, que
    es justo lo que D-1 quiere evitar.
    """
    await service.create(make_request(HASH_A))

    result = await service.find_by_hash(HASH_A.upper())

    assert result.exists is False


# --------------------------------------- by-checksum (contrato del Orquestador)


async def test_get_by_checksum_returns_the_document_when_it_exists(
    service: DocumentService,
) -> None:
    """Contrato del Orquestador: `GET /by-checksum/{hash}` devuelve el documento
    cuando el hash ya está persistido, para que el orquestador responda `REUSED`.
    """
    created = await service.create(make_request(HASH_A))

    found = await service.get_by_checksum(HASH_A)

    assert found.id == created.id
    assert found.pdf_hash == HASH_A
    assert found.filename == created.filename
    assert found.page_count == created.page_count


async def test_get_by_checksum_raises_not_found_when_absent(
    service: DocumentService,
) -> None:
    """Contrato del Orquestador: a diferencia de `by-hash`, un hash desconocido en
    `by-checksum` **es** un 404. El orquestador lo usa como señal de "no lo tengo,
    extraer" (su cliente lo mapea a `ErrDocumentNotFound`), no como `exists: false`.
    """
    with pytest.raises(ResourceNotFoundException):
        await service.get_by_checksum(HASH_A)


# --------------------------------------------------------------------------- US-2


async def test_create_persists_and_returns_the_new_document(service: DocumentService) -> None:
    created = await service.create(make_request(HASH_A))

    assert created.pdf_hash == HASH_A
    assert created.id


async def test_create_raises_duplicate_on_a_repeated_hash(
    service: DocumentService,
) -> None:
    """US-2: el `pdf_hash` repetido debe acabar en 409.

    El test verifica la **traducción**, que es lo que hace el servicio. Que el
    índice único de Mongo rechace el duplicado de verdad lo prueba la integración.
    """
    await service.create(make_request(HASH_A))

    with pytest.raises(DuplicateResourceException):
        await service.create(make_request(HASH_A))


async def test_create_propagates_the_client_supplied_timestamp(
    service: DocumentService,
) -> None:
    """Si el productor manda `uploaded_at`, se respeta: puede estar reintentando una
    subida con la hora original y no quiere que se pierda.
    """
    moment = datetime(2026, 4, 1, 12, 0, tzinfo=UTC)

    created = await service.create(make_request(HASH_A, uploaded_at=moment))

    assert created.uploaded_at == moment


async def test_create_generates_the_timestamp_when_absent(service: DocumentService) -> None:
    """SPEC.md §1: el servidor genera `uploaded_at` si el payload no lo trae.

    Confiar en que el productor siempre lo envía sería frágil, y un `uploaded_at`
    nulo rompería el orden del listado, que es la consulta más usada.
    """
    created = await service.create(make_request(HASH_A))

    assert created.uploaded_at is not None
    assert created.uploaded_at.tzinfo is not None


async def test_create_rejects_a_malformed_hash_before_reaching_the_repository(
    service: DocumentService,
) -> None:
    """D-1 con efecto práctico: un hash inválido es un 422 de validación, y no debe
    llegar al repositorio.

    Si llegara, el índice único no lo detectaría (no es un duplicado) y Mongo lo
    guardaría tal cual, dejando basura que ningún cliente puede consultar después.
    """
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        await service.create(make_request(pdf_hash="NO-ES-UN-HASH"))


# --------------------------------------------------------------------------- US-3


async def test_list_documents_returns_documents_newest_first(
    service: DocumentService,
) -> None:
    """US-3: el listado empieza por el más nuevo."""
    older = await service.create(make_request(HASH_A, uploaded_at=datetime(2026, 1, 1, tzinfo=UTC)))
    newer = await service.create(make_request(HASH_B, uploaded_at=datetime(2026, 5, 1, tzinfo=UTC)))

    page = await service.list_documents(limit=10, offset=0)

    assert [item.id for item in page.items] == [newer.id, older.id]


async def test_list_documents_reports_the_total_not_the_window_size(
    service: DocumentService,
) -> None:
    """Un cliente que pagina necesita saber cuántas hay en total para saber cuándo
    parar. Devolver el tamaño de la ventana le hace terminar antes de tiempo.
    """
    for index, hash_value in enumerate([HASH_A, HASH_B, HASH_C]):
        await service.create(
            make_request(hash_value, uploaded_at=datetime(2026, 1, 1 + index, tzinfo=UTC))
        )

    page = await service.list_documents(limit=2, offset=0)

    assert page.total == 3
    assert len(page.items) == 2


async def test_list_documents_returns_an_empty_page_when_there_is_nothing(
    service: DocumentService,
) -> None:
    """Listar una colección vacía es un caso normal, no un error. El orquestador lo
    hace en cada arranque.
    """
    page = await service.list_documents(limit=20, offset=0)

    assert page.items == []
    assert page.total == 0


async def test_list_documents_echoes_limit_and_offset(
    service: DocumentService,
) -> None:
    """La respuesta incluye `limit` y `offset`: el cliente los usa para la siguiente
    petición, y tenerlos hardcodeados en el cliente rompe en cuanto cambie un valor
    por defecto del servidor.
    """
    page = await service.list_documents(limit=5, offset=10)

    assert page.limit == 5
    assert page.offset == 10


# --------------------------------------------------------------------------- US-4


async def test_get_by_id_returns_the_document(service: DocumentService) -> None:
    created = await service.create(make_request(HASH_A))

    found = await service.get_by_id(created.id)

    assert found.id == created.id
    assert found.pdf_hash == HASH_A


async def test_get_by_id_raises_not_found_for_a_missing_document(
    service: DocumentService,
) -> None:
    """SC-06: ObjectId válido e inexistente es 404, no 500 ni una respuesta vacía."""
    with pytest.raises(ResourceNotFoundException):
        await service.get_by_id(MISSING_OBJECT_ID)


async def test_get_by_id_raises_invalid_id_for_a_malformed_one(
    service: DocumentService,
) -> None:
    """SC-06: un id que no es ObjectId es 400.

    Es un caso distinto del anterior y por eso una excepción distinta: con un
    identificador mal formado el servicio ni siquiera ha consultado nada, así que
    la respuesta correcta es "tu petición es incorrecta", no "no existe".
    """
    with pytest.raises(InvalidDocumentIdException):
        await service.get_by_id(MALFORMED_OBJECT_ID)


async def test_delete_removes_the_document(service: DocumentService) -> None:
    """US-4."""
    created = await service.create(make_request(HASH_A))

    await service.delete(created.id)

    with pytest.raises(ResourceNotFoundException):
        await service.get_by_id(created.id)


async def test_delete_raises_not_found_when_already_deleted(
    service: DocumentService,
) -> None:
    """SC-08: la segunda llamada da 404.

    Es deliberado y no un descuido. Un 204 en la segunda llamada impediría a un
    cliente que reintenta por timeout distinguir "lo borré yo" de "lo borró otro",
    que es justo la información que necesita saber para continuar.
    """
    created = await service.create(make_request(HASH_A))
    await service.delete(created.id)

    with pytest.raises(ResourceNotFoundException):
        await service.delete(created.id)


async def test_delete_raises_invalid_id_for_a_malformed_one(
    service: DocumentService,
) -> None:
    with pytest.raises(InvalidDocumentIdException):
        await service.delete(MALFORMED_OBJECT_ID)


async def test_hash_becomes_reusable_after_delete(service: DocumentService) -> None:
    """Tras un borrado, el mismo PDF se puede volver a subir.

    Es un caso real: el orquestador reintenta una subida que falló después de
    persistir, y si el hash quedara bloqueado el sistema se quedaría sin salida.
    """
    created = await service.create(make_request(HASH_A))
    await service.delete(created.id)

    recreated = await service.create(make_request(HASH_A))

    assert recreated.id != created.id
