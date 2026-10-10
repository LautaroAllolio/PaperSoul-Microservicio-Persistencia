"""Traducción entre el modelo de persistencia y los DTOs de la API.

Vive aparte de `document.py` porque los DTOs no deben saber nada de la capa de
datos: un esquema que importa `Page` del repositorio para poder envolverlo está
atado a un detalle interno que no es parte del contrato público.

Y por la misma razón el mapeo no se mete en `Page` como un método: `Page` es un
sobre genérico, y enseñarle a convertir documentos PDF lo convertiría en un
concepto de este dominio.

## La excepción: `models` sí se importa aquí

Este módulo es el puente entre las dos fronteras, y por eso es el único punto de `app/`
donde el DTO de transporte y el documento de persistencia se tocan. Es una
dependencia *explícita y localizada*, no una regla: si aparece en otro módulo,
`tests/test_architecture.py` lo señala. La alternativa —duplicar los tipos de un lado
— es peor que la excepción.
"""

from app.models.pdf_document import PdfDocumentFields, StoredDocument
from app.schemas.document import (
    ChecksumMatchResponse,
    DocumentCreateRequest,
    DocumentListResponse,
    DocumentPersistedResponse,
    DocumentResponse,
)
from app.schemas.pagination import Page


def to_response(document: StoredDocument) -> DocumentResponse:
    """Modelo de persistencia → DTO de respuesta completa."""
    return DocumentResponse(**document.model_dump())


def to_persisted_response(document: StoredDocument) -> DocumentPersistedResponse:
    """Modelo de persistencia → confirmación del 201.

    Proyecta sólo tres campos a propósito, no un subconjunto arbitrario: la
    respuesta del 201 existe para confirmar la persistencia, y devolver los 10 MB
    de `extracted_text` que el cliente acaba de enviar duplicaría el tráfico sin
    aportar nada.
    """
    return DocumentPersistedResponse(
        id=document.id,
        pdf_hash=document.pdf_hash,
        uploaded_at=document.uploaded_at,
    )


def to_checksum_match_response(document: StoredDocument) -> ChecksumMatchResponse:
    """Modelo de persistencia → documento del `by-checksum` del Orquestador.

    Proyecta los campos que el Orquestador necesita para su respuesta `REUSED`
    (véase `ChecksumMatchResponse`); si devolviera el `extracted_text` entero,
    duplicaría el tráfico que el cliente acaba de generar.
    """
    return ChecksumMatchResponse(
        id=document.id,
        pdf_hash=document.pdf_hash,
        filename=document.filename,
        page_count=document.page_count,
    )


def to_list_response(page: Page[StoredDocument]) -> DocumentListResponse:
    """`Page` del repositorio → respuesta del listado, con los metadatos de paginación.

    `total`, `limit` y `offset` se copian tal cual. El cliente los usa para la
    siguiente petición, y recalcularlos aquí abriría la puerta a que el DTO y el
    repositorio contasen cosas distintas.
    """
    return DocumentListResponse(
        items=[to_response(item) for item in page.items],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


def to_fields(request: DocumentCreateRequest) -> PdfDocumentFields:
    """DTO de creación → reglas de dominio, que son las que valida el modelo.

    El paso es explícito y no un `**request.model_dump()` en el servicio: la
    validación ocurre en `PdfDocumentFields`, y el repositorio recibe siempre un
    objeto ya validado. Así no hay un camino en el que se persista algo sin pasar
    por las reglas.

    `uploaded_at` ausente **se quita del diccionario**, no se pasa como `None`.
    Pasarlo explícitamente anula el `default_factory` del modelo y falla la
    validación: el valor por defecto sólo se aplica cuando la clave no está.

    No se usa `exclude_none=True` porque borraría también `text_hash`, que sí debe
    persistirse como nulo: son dos ausencias con significados opuestos, una la
    genera el servidor y la otra la pide el cliente.
    """
    payload = request.model_dump()
    if payload["uploaded_at"] is None:
        del payload["uploaded_at"]
    return PdfDocumentFields(**payload)
