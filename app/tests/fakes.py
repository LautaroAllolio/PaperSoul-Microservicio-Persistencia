"""Fakes de prueba: implementaciones en memoria de los contratos de datos.

Se escriben a mano, **no con `unittest.mock`** (SPEC.md §8.2). La razón es
concreta: un `Mock` configurado con `return_value=lo_que_el_test_espera` pasa
aunque el código real esté roto, porque no ejecuta nada. Un fake con la regla de
unicidad implementada de verdad puede fallar, que es lo único que lo hace útil.

Consecuencia práctica: `FakePdfRepository` **falla** cuando el contrato cambia, y
`isinstance(fake, PdfRepository)` en los tests lo detecta antes de que se llegue a
la implementación real.
"""

from itertools import count

from app.exceptions.domain import (
    DuplicateResourceException,
    InvalidDocumentIdException,
    ResourceNotFoundException,
)
from app.models.pdf_document import PdfDocumentFields, StoredDocument
from app.repositories.base import Page
from app.repositories.pdf_repository import PdfRepository

OBJECT_ID_HEX_LENGTH = 24

# Contador monótono para generar ids. Se usa en vez de `uuid4()` porque los
# ObjectId de Mongo **crecen con el tiempo**, y el test de desempate de
# `uploaded_at` depende de que el orden por id coincida con el orden de inserción.
# Con ids aleatorios, dos documentos del mismo instante podrían ordenarse al revés
# entre ejecuciones y el test sería intermitente sin causa aparente.
_id_counter = count(1)


def _next_object_id() -> str:
    """Siguiente id con la forma de un ObjectId: 24 caracteres hexadecimales."""
    return f"{next(_id_counter):0{OBJECT_ID_HEX_LENGTH}x}"


def is_object_id(value: str) -> bool:
    """¿Tiene la forma de un ObjectId de Mongo?

    Réplica de la comprobación que hace el driver, sin la dependencia: 24 hex.
    Deliberadamente *sólo* de forma, sin comprobar que el timestamp codificado sea
    plausible. El driver acepta cualquier hex de 24 caracteres, y un servicio que
    fuera más estricto que Mongo rechazaría ids que la base de datos sí aceptaría.
    """
    if len(value) != OBJECT_ID_HEX_LENGTH:
        return False
    try:
        int(value, 16)
    except ValueError:
        return False
    return True


class FakePdfRepository(PdfRepository):
    """Colección de PDF en memoria, con la semántica del contrato implementada.

    Guarda `StoredDocument` en un `dict` indexado por id. La unicidad de
    `pdf_hash` se comprueba con un segundo índice, igual que hace el índice único
    de Mongo: para que este fake pueda **fallar** cuando la deduplicación se rompa.
    """

    def __init__(self) -> None:
        self._documents: dict[str, StoredDocument] = {}
        self._by_hash: dict[str, str] = {}

    async def create(self, fields: PdfDocumentFields) -> StoredDocument:
        if fields.pdf_hash in self._by_hash:
            raise DuplicateResourceException(
                f"ya existe un documento con pdf_hash {fields.pdf_hash}"
            )

        stored = StoredDocument(id=_next_object_id(), **fields.model_dump())
        self._documents[stored.id] = stored
        self._by_hash[stored.pdf_hash] = stored.id
        return stored

    async def get_by_hash(self, pdf_hash: str) -> StoredDocument | None:
        document_id = self._by_hash.get(pdf_hash)
        return self._documents.get(document_id) if document_id else None

    async def get_by_id(self, document_id: str) -> StoredDocument:
        self._require_valid_id(document_id)

        stored = self._documents.get(document_id)
        if stored is None:
            raise ResourceNotFoundException(f"no existe un documento con id {document_id}")
        return stored

    async def list_paginated(self, limit: int, offset: int) -> Page[StoredDocument]:
        # Orden `uploaded_at` DESC, `_id` DESC. El desempate por id no es decorativo:
        # sin él, dos documentos con el mismo milisegundo pueden cambiar de posición
        # entre dos peticiones y un cliente que pagina los vería repetidos o
        # saltados (SC-05).
        ordered = sorted(
            self._documents.values(),
            key=lambda document: (document.uploaded_at, document.id),
            reverse=True,
        )
        return Page(
            items=ordered[offset : offset + limit],
            total=len(ordered),
            limit=limit,
            offset=offset,
        )

    async def delete(self, document_id: str) -> None:
        self._require_valid_id(document_id)

        stored = self._documents.pop(document_id, None)
        if stored is None:
            raise ResourceNotFoundException(f"no existe un documento con id {document_id}")
        del self._by_hash[stored.pdf_hash]

    @staticmethod
    def _require_valid_id(document_id: str) -> None:
        """Valida la forma del id **antes** de tocar el almacén.

        Consultar con un id inválido convertiría un error del cliente en un 500, y
        el mensaje de Mongo al respecto no está pensado para un cliente HTTP.
        """
        if not is_object_id(document_id):
            raise InvalidDocumentIdException(f"'{document_id}' no es un ObjectId válido")

    def count(self) -> int:
        """Número de documentos guardados.

        No forma parte del contrato: es una aserción propia de los tests del fake,
        para poder afirmar sobre la colección sin recorrerla.
        """
        return len(self._documents)
