"""Casos de uso del servicio de documentos.

Capa 2: orquesta. No sabe nada de HTTP ni de queries de Mongo, y por eso se puede
probar entero con un fake (SPEC.md §8.1).

Recibe el repositorio por constructor y no lo busca en un singleton ni en un
contenedor global. Es lo que permite que un test construya un servicio real sobre
un fake sin montar la aplicación.
"""

from app.exceptions.domain import ResourceNotFoundException
from app.models.pdf_document import StoredDocument
from app.repositories.pdf_repository import PdfRepository
from app.schemas.document import DocumentCreateRequest, HashExistsResponse
from app.schemas.mappers import to_fields
from app.schemas.pagination import Page


class DocumentService:
    """Casos de uso de la colección de PDF.

    Todos los métodos son asíncronos porque el repositorio lo es, aunque la lógica
    que hay aquí dentro sea síncrona. Es el precio de mantener el mismo contrato
    entre la implementación real y el fake, y evita que aparezca un `sync`/`async`
    distinto en cada capa.
    """

    def __init__(self, repository: PdfRepository) -> None:
        self._repository = repository

    async def create(self, request: DocumentCreateRequest) -> StoredDocument:
        """US-2: persiste un documento nuevo.

        El `DuplicateResourceException` del repositorio sube sin tocar. No es que
        el servicio no lo controle: es que no tiene nada que añadir. El 409 lo
        decide el handler a partir del status de la excepción, y si el servicio
        intentara decidirlo volvería a coupling el negocio con el transporte.
        """
        return await self._repository.create(to_fields(request))

    async def find_by_hash(self, pdf_hash: str) -> HashExistsResponse:
        """US-1: consulta por `pdf_hash`, y responde siempre.

        No propaga errores de "no encontrado" porque no los hay: un hash
        desconocido es `exists: false`. El orquestador pregunta esto en su flujo
        normal, y obligarlo a capturar excepciones para una consulta rutinaria
        convertiría cada `POST` en un bloque de `try/except`.
        """
        document = await self._repository.get_by_hash(pdf_hash)

        if document is None:
            return HashExistsResponse(exists=False)

        return HashExistsResponse(
            exists=True,
            id=document.id,
            uploaded_at=document.uploaded_at,
        )

    async def get_by_checksum(self, pdf_hash: str) -> StoredDocument:
        """Contrato del Orquestador: consulta por `pdf_hash` distinguiendo exista o no.

        A diferencia de `find_by_hash`, aquí "no existe" **es** un error: el
        Orquestador usa el 404 de `GET /by-checksum/{pdf_hash}` como señal de
        control de flujo del dedup (no lo tiene → extraer), y su cliente lo mapea a
        `ErrDocumentNotFound`. Por eso este método lanza
        `ResourceNotFoundException` en vez de responder `exists: false`.
        """
        document = await self._repository.get_by_hash(pdf_hash)
        if document is None:
            raise ResourceNotFoundException(f"no existe un documento con pdf_hash {pdf_hash}")
        return document

    async def get_by_id(self, document_id: str) -> StoredDocument:
        """Devuelve un documento por id.

        SC-06 distingue los dos fallos y ambos llegan intactos al handler como 404 y
        400 respectivamente. El servicio no los unifica: son respuestas distintas
        porque un id mal formado significa que el cliente se equivocó, y uno bien
        formado que no existe significa que el documento no está.
        """
        return await self._repository.get_by_id(document_id)

    async def list_documents(self, limit: int, offset: int) -> Page[StoredDocument]:
        """US-3: ventana de documentos, de más nuevo a más viejo."""
        return await self._repository.list_paginated(limit=limit, offset=offset)

    async def delete(self, document_id: str) -> None:
        """US-4: borra un documento.

        SC-08: la segunda llamada lanza `ResourceNotFoundException` y el handler la
        sirve como 404. Es una decisión, no una omisión; el comentario del contrato
        explica por qué no se hace idempotente.
        """
        await self._repository.delete(document_id)
