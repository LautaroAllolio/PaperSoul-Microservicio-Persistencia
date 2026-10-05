"""Contrato de persistencia de documentos PDF y su implementaciÃ³n Beanie.

El servicio (capa 2) habla con `PdfRepository`, nunca con Mongo. Dos razones, y no
son la misma:

- **Testeable**: el servicio se prueba con un fake en memoria, sin Docker. Es lo
  que permite que casi toda la suite corra en cada commit en vez de sÃ³lo en la
  mÃ¡quina que tiene Mongo.
- **Desacoplado del ODM**: al migrar de Beanie a pymongo (Â§Escapatoria de la
  SPEC), sÃ³lo cambia `BeaniePdfRepository`. El contrato, el servicio y los routers
  no se tocan.

`PdfRepository` es un `Protocol` y no una clase base: asÃ­ el fake y la
implementaciÃ³n real no heredan de nada, y aÃ±adir un mÃ©todo al contrato hace que
ambos fallen en el chequeo en vez de que uno se quede atrÃ¡s en silencio.
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
    """Operaciones que la capa de negocio necesita de la colecciÃ³n de PDF.

    Devuelve `StoredDocument` y `PdfDocumentFields`, nunca `PdfDocument`: el
    documento de Beanie no se puede construir sin una conexiÃ³n abierta, y un
    contrato que lo devolviera obligarÃ­a a tener Mongo para poder testear.
    """

    async def create(self, fields: PdfDocumentFields) -> StoredDocument:
        """Persiste un documento nuevo.

        Lanza `DuplicateResourceException` si el `pdf_hash` ya existe. La unicidad
        la aplica la base de datos mediante el Ã­ndice Ãºnico, no un `if` previo: con
        dos peticiones concurrentes, un `get_by_hash` seguido de `create` deja pasar
        las dos.
        """
        ...

    async def get_by_hash(self, pdf_hash: str) -> StoredDocument | None:
        """Devuelve el documento con ese `pdf_hash`, o `None` si no existe.

        `None` y no una excepciÃ³n: no encontrar **no** es un error, es la respuesta
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
        """Ventana de la colecciÃ³n, en orden `uploaded_at` DESC, `_id` DESC."""
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
    # milisegundos, asÃ­ que dos documentos del mismo instante empatan y, sin Ã©l, el
    # orden puede variar entre dos peticiones idÃ©nticas. Un cliente que pagina
    # verÃ­a documentos repetidos o saltados (SC-05).
    ("_id", SortDirection.DESCENDING),
]


class BeaniePdfRepository(BaseRepository[StoredDocument]):
    """ImplementaciÃ³n real sobre Beanie/Motor.

    Vive aquÃ­, y no en `models/`, porque es el punto donde el dominio se encuentra
    con el ODM. Es tambiÃ©n el **Ãºnico** repositorio que habrÃ­a que reescribir al
    migrar a pymongo, junto con `core/database.py`.

    No tiene tests unitarios: sus mÃ©todos necesitan una colecciÃ³n inicializada. La
    semÃ¡ntica se prueba en `tests/integration/` contra Mongo real, y aquÃ­ se
    comprueba sÃ³lo lo que no necesita base de datos (el orden declarado).
    """

    async def create(self, fields: PdfDocumentFields) -> StoredDocument:
        document = PdfDocument(**fields.model_dump())
        try:
            await document.insert()
        except DuplicateKeyError as error:
            # TraducciÃ³n de la excepciÃ³n del driver a la del dominio, y nada mÃ¡s:
            # el mensaje de Mongo menciona el Ã­ndice y el namespace, detalles que no
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
        # cada paso y es fÃ¡cil perder una etapa por el camino sin que se note.
        #
        # `count` es una query extra (OT-6, aceptado de forma explÃ­cita). Se lanza
        # con `gather` junto a la ventana porque son independientes: en serie
        # costarÃ­a el doble de latencia sin motivo.
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

    SC-06: el corte 400/422 de la SPEC sitÃºa aquÃ­ el 400, porque ningÃºn payload
    vÃ¡lido produce un id mal formado. Validar **antes** de consultar es lo que evita
    que un error del cliente se convierta en un 500.
    """
    if not ObjectId.is_valid(document_id):
        raise InvalidDocumentIdException(f"'{document_id}' no es un ObjectId vÃ¡lido")
    return ObjectId(document_id)


def _to_stored(document: PdfDocument) -> StoredDocument:
    """Proyecta el documento de Beanie al tipo que devuelve el contrato.

    El `id` se serializa a `str` porque en el tipo de retorno es texto, y porque un
    `ObjectId` en el JSON saldrÃ­a como `{"$oid": ...}` si se serializara sin
    conversiÃ³n. `uploaded_at` no se toca: el cliente se crea con `tz_aware=True`, de
    modo que llega con offset UTC y D-2 se cumple sin interveneir aquÃ­ (SC-07).
    """
    return StoredDocument(id=str(document.id), **document.model_dump(exclude={"id"}))
