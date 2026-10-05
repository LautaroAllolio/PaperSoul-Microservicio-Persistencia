"""Tests de los mappers entre el modelo de persistencia y los DTOs (S3, tarea 3.3).

Son funciones puras: sin base de datos, sin red y sin FastAPI. Y son el punto donde
un dato puede perder el offset UTC o el `id` sin que nadie lo note, así que se
prueban directamente en vez de confiar en que algún endpoint las ejercite.
"""

from datetime import UTC, datetime

from app.models.pdf_document import PdfDocumentFields, StoredDocument
from app.repositories.base import Page
from app.schemas.document import DocumentCreateRequest
from app.schemas.mappers import (
    to_fields,
    to_list_response,
    to_persisted_response,
    to_response,
)

PDF_HASH = "a" * 64
TEXT_HASH = "b" * 64
DOCUMENT_ID = "507f1f77bcf86cd799439011"

MOMENT = datetime(2026, 6, 1, 10, 30, tzinfo=UTC)


def make_fields(**overrides: object) -> PdfDocumentFields:
    fields: dict[str, object] = {
        "filename": "contrato.pdf",
        "extracted_text": "texto extraído",
        "extraction_method": "pymupdf",
        "page_count": 3,
        "pdf_hash": PDF_HASH,
        "uploaded_at": MOMENT,
    }
    fields.update(overrides)
    return PdfDocumentFields(**fields)


def make_stored(**overrides: object) -> StoredDocument:
    return StoredDocument(id=DOCUMENT_ID, **make_fields(**overrides).model_dump())


def test_to_fields_carries_every_domain_field() -> None:
    """Un mapper que pierde un campo es un fallo silencioso: el dato no está en la
    respuesta, pero el servicio funcionó sin errores."""
    request = DocumentCreateRequest(
        filename="contrato.pdf",
        extracted_text="texto extraído",
        extraction_method="pymupdf",
        page_count=3,
        pdf_hash=PDF_HASH,
        text_hash=TEXT_HASH,
    )

    fields = to_fields(request)

    assert fields.filename == "contrato.pdf"
    assert fields.extracted_text == "texto extraído"
    assert fields.page_count == 3
    assert fields.pdf_hash == PDF_HASH
    assert fields.text_hash == TEXT_HASH


def test_to_fields_lets_the_model_generate_a_missing_timestamp() -> None:
    """`uploaded_at` ausente se **omite** del diccionario para que lo rellene el
    `default_factory` del modelo.

    Pasarlo como `None` anularía el valor por defecto y haría fallar la validación,
    porque el campo es `datetime`, no `datetime | None`.
    """
    request = DocumentCreateRequest(
        filename="contrato.pdf",
        extracted_text="texto extraído",
        extraction_method="pymupdf",
        page_count=3,
        pdf_hash=PDF_HASH,
    )

    fields = to_fields(request)

    assert fields.uploaded_at is not None
    assert fields.uploaded_at.utcoffset().total_seconds() == 0


def test_to_fields_keeps_a_client_supplied_timestamp() -> None:
    """Si el productor manda `uploaded_at`, viaja tal cual.

    Es la otra mitad de la regla: el orquestador puede estar reintentando una
    subida con la hora original, y sustituirla por la de ahora perdería cuándo se
    procesó el documento.
    """
    request = DocumentCreateRequest(
        filename="contrato.pdf",
        extracted_text="texto extraído",
        extraction_method="pymupdf",
        page_count=3,
        pdf_hash=PDF_HASH,
        uploaded_at=MOMENT,
    )

    assert to_fields(request).uploaded_at == MOMENT


def test_to_fields_keeps_a_null_text_hash() -> None:
    """`text_hash` ausente **sí** se persiste, como `None`.

    Es el otro lado de la regla anterior: usar `exclude_none=True` para arreglar el
    timestamp se comería también el `text_hash`, que es una ausencia que el
    productor sí ha pedido explícitamente.
    """
    request = DocumentCreateRequest(
        filename="contrato.pdf",
        extracted_text="texto extraído",
        extraction_method="pymupdf",
        page_count=3,
        pdf_hash=PDF_HASH,
    )

    assert to_fields(request).text_hash is None


def test_to_response_keeps_the_id_and_the_utc_offset() -> None:
    """SC-07 en el punto de traducción: el offset se conserva. Si se pierde aquí,
    la respuesta sale sin zona y el mismo documento se lee de forma distinta en
    cada cliente.
    """
    response = to_response(make_stored())

    assert response.id == DOCUMENT_ID
    assert response.uploaded_at == MOMENT
    assert response.uploaded_at.utcoffset() is not None


def test_to_persisted_response_returns_only_the_confirmation_fields() -> None:
    """El 201 no devuelve `extracted_text`: puede tener 10 MB y el cliente acaba de
    enviarlo. Duplicarlo sería gastar el ancho de banda para nada.
    """
    response = to_persisted_response(make_stored())

    assert response.id == DOCUMENT_ID
    assert response.pdf_hash == PDF_HASH
    assert response.uploaded_at == MOMENT
    assert "extracted_text" not in type(response).model_fields


def test_to_list_response_preserves_the_pagination_metadata() -> None:
    """`total`, `limit` y `offset` se copian sin recalcular. Si el DTO los calculara
    por su cuenta, el cliente recibiría un total distinto del que el repositorio
    sabe, y la paginación se saltaría documentos sin avisar.
    """
    page = Page[StoredDocument](
        items=[make_stored()],
        total=42,
        limit=1,
        offset=41,
    )

    response = to_list_response(page)

    assert response.total == 42
    assert response.limit == 1
    assert response.offset == 41
    assert len(response.items) == 1


def test_to_list_response_maps_every_item() -> None:
    page = Page[StoredDocument](
        items=[make_stored(), make_stored(pdf_hash="c" * 64)],
        total=2,
        limit=2,
        offset=0,
    )

    response = to_list_response(page)

    assert [item.pdf_hash for item in response.items] == [PDF_HASH, "c" * 64]
