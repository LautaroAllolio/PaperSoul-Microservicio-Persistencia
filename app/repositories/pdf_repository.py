"""Contrato de persistencia de documentos PDF y su implementación Beanie.

El servicio (capa 2) habla con `PdfRepository`, nunca con Mongo. Dos razones, y no
son la misma:

- **Testeable**: el servicio se prueba con un fake en memoria, sin Docker. Es lo
  que permite que casi toda la suite corra en cada commit en vez de sólo en la
  máquina que tiene Mongo.
- **Desacoplado del ODM**: al migrar de Beanie a pymongo (§Escapatoria de la
  SPEC), sólo cambia `BeaniePdfRepository`. El contrato, el servicio y los routers
  no se tocan.

`PdfRepository` es un `Protocol` y no una clase base: así el fake y la
implementación real no heredan de nada, y añadir un método al contrato hace que
ambos fallen en el chequeo en vez de que uno se quede atrás en silencio.
"""

import asyncio
from typing import Protocol, runtime_checkable

from beanie import SortDirection
from bson import ObjectId
from pymongo.errors import DuplicateKeyError

from app.exceptions.domain import (
    DuplicateResourceException,
    InvalidDocumentIdException,
    ResourceNotFoundException,
)
from app.models.pdf_document import PdfDocument, PdfDocumentFields, StoredDocument
from app.repositories.base import BaseRepository, Page


@runtime_checkable
class PdfRepository(Protocol):
    """Operaciones que la capa de negocio necesita de la colección de PDF.

    Devuelve `StoredDocument` y `PdfDocumentFields`, nunca `PdfDocument`: el
    documento de Beanie no se puede construir sin una conexión abierta, y un
    contrato que lo devolviera obligaría a tener Mongo para poder testear.
    """

    async def create(self, fields: PdfDocumentFields) -> StoredDocument:
        """Persiste un documento nuevo.

        Lanza `DuplicateResourceException` si el `pdf_hash` ya existe. La unicidad
        la aplica la base de datos mediante el índice único, no un `if` previo: con
        dos peticiones concurrentes, un `get_by_hash` seguido de `create` deja pasar
        las dos.
        """
        ...

    async def get_by_hash(self, pdf_hash: str) -> StoredDocument | None:
        """Devuelve el documento con ese `pdf_hash`, o `None` si no existe.

        `None` y no una excepción: no encontrar **no** es un error, es la respuesta
        normal de `GET /by-hash/{pdf_hash}`, que responde 200 con `exists: false`.
        """
        ...

    async def get_by_id(self, document_id: str) -> StoredDocument:
        """Devuelve el documento con ese id.

        Lanza `InvalidDocumentIdException` si el id no tiene forma de ObjectId
        (SC-06, un 400) y `ResourceNotFoundException` si no existe (un 404).
        """
        ...

    async def list_paginated(self, limit: int, offset: int) -> Page[StoredDocument]:
        """Ventana de la colección, en orden `uploaded_at` DESC, `_id` DESC."""
        ...

    async def delete(self, document_id: str) -> None:
        """Borra el documento.

        Lanza las mismas dos excepciones que `get_by_id`. Borra en lugar de marcar
        con `soft delete` porque el orquestador es la única fuente de verdad y debe
        poder reintentar la subida si el borrado fue accidental.
        """
        ...


LISTING_SORT: list[tuple[str, SortDirection]] = [
    ("uploaded_at", SortDirection.DESCENDING),
    # El desempate por `_id` no es decorativo: `uploaded_at` tiene granularidad de
    # milisegundos, así que dos documentos del mismo instante empatan y, sin él, el
    # orden puede variar entre dos peticiones idénticas. Un cliente que pagina
    # vería documentos repetidos o saltados (SC-05).
    ("_id", SortDirection.DESCENDING),
]


class BeaniePdfRepository(BaseRepository[StoredDocument]):
    """Implementación real sobre Beanie/Motor.

    Vive aquí, y no en `models/`, porque es el punto donde el dominio se encuentra
    con el ODM. Es también el **único** repositorio que habría que reescribir al
    migrar a pymongo, junto con `core/database.py`.

    No tiene tests unitarios: sus métodos necesitan una colección inicializada. La
    semántica se prueba en `tests/integration/` contra Mongo real, y aquí se
    comprueba sólo lo que no necesita base de datos (el orden declarado).
    """

    async def create(self, fields: PdfDocumentFields) -> StoredDocument:
        document = PdfDocument(**fields.model_dump())
        try:
            await document.insert()
        except DuplicateKeyError as error:
            # Traducción de la excepción del driver a la del dominio, y nada más:
            # el mensaje de Mongo menciona el índice y el namespace, detalles que no
            # significan nada para el cliente. El 409 lo decide el handler.
            raise DuplicateResourceException(
                f"ya existe un documento con pdf_hash {fields.pdf_hash}"
            ) from error
        return _to_stored(document)

    async def get_by_hash(self, pdf_hash: str) -> StoredDocument | None:
        document = await PdfDocument.find_one(PdfDocument.pdf_hash == pdf_hash)

        return _to_stored(document) if document is not None else None

    async def get_by_id(self, document_id: str) -> StoredDocument:
        object_id = _parse_object_id(document_id)

        document = await PdfDocument.get(object_id)
        if document is None:
            raise ResourceNotFoundException(f"no existe un documento con id {document_id}")
        return _to_stored(document)

    async def list_paginated(self, limit: int, offset: int) -> Page[StoredDocument]:
        # `sort`, `skip` y `limit` van en la misma llamada en lugar de encadenar
        # `.sort().skip().limit()`: el encadenado devuelve un objeto distinto en
        # cada paso y es fácil perder una etapa por el camino sin que se note.
        #
        # `count` es una query extra (OT-6, aceptado de forma explícita). Se lanza
        # con `gather` junto a la ventana porque son independientes: en serie
        # costaría el doble de latencia sin motivo.
        documents, total = await asyncio.gather(
            PdfDocument.find(sort=LISTING_SORT, skip=offset, limit=limit).to_list(),
            PdfDocument.find_all().count(),
        )
        return Page(
            items=[_to_stored(document) for document in documents],
            total=total,
            limit=limit,
            offset=offset,
        )

    async def delete(self, document_id: str) -> None:
        object_id = _parse_object_id(document_id)

        document = await PdfDocument.get(object_id)
        if document is None:
            raise ResourceNotFoundException(f"no existe un documento con id {document_id}")
        await document.delete()


def _parse_object_id(document_id: str) -> ObjectId:
    """Convierte el id a `ObjectId`, o lanza `InvalidDocumentIdException`.

    SC-06: el corte 400/422 de la SPEC sitúa aquí el 400, porque ningún payload
    válido produce un id mal formado. Validar **antes** de consultar es lo que evita
    que un error del cliente se convierta en un 500.
    """
    if not ObjectId.is_valid(document_id):
        raise InvalidDocumentIdException(f"'{document_id}' no es un ObjectId válido")
    return ObjectId(document_id)


def _to_stored(document: PdfDocument) -> StoredDocument:
    """Proyecta el documento de Beanie al tipo que devuelve el contrato.

    El `id` se serializa a `str` porque en el tipo de retorno es texto, y porque un
    `ObjectId` en el JSON saldría como `{"$oid": ...}` si se serializara sin
    conversión. `uploaded_at` no se toca: el cliente se crea con `tz_aware=True`, de
    modo que llega con offset UTC y D-2 se cumple sin intervenir aquí (SC-07).
    """
    return StoredDocument(id=str(document.id), **document.model_dump(exclude={"id"}))
