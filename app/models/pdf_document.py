"""Documento persistido de MongoDB.

Es el modelo de **persistencia**, no el de transporte. Mezcla campos que viajan
por la API con detalles de almacenamiento (`_id`, índices). Los DTOs que viajan
son los de `schemas/`.

## Por qué dos clases y no una

`beanie.Document.__init__` llama incondicionalmente a `get_motor_collection()`,
que lanza `CollectionWasNotInitialized` si no se ha ejecutado `init_beanie`. Es
decir: **una instancia de `Document` no se puede construir sin una base de datos
conectada**, ni siquiera para validar un campo.

Eso convertía cada test de validación de campo en un test de integración que
necesita Docker. La primera causa de un fallo en producción (un `pdf_hash` en
mayúsculas, un `extracted_text` vacío) no se podía comprobar sin levantar Mongo.

De ahí la separación:

- `PdfDocumentFields`: las reglas de dominio, en un `BaseModel` puro. Se instancian
  y se validan sin ninguna conexión.
- `PdfDocument`: hereda de esas reglas y añade el motor de Beanie. Es el único que
  se persiste y el único que necesita la base de datos.

No es una abstracción gratuita: es la que hace testeable el 90% del modelo sin
Docker, que es la diferencia entre un gate que corre en cada commit y uno que sólo
corre en la máquina del que tiene Docker instalado.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated

from beanie import Document
from pydantic import BaseModel, ConfigDict, Field, field_validator
from pymongo import IndexModel

# D-1: SHA-256 en hexadecimal, exactamente 64 caracteres, **sin flag `i`**.
#
# El rechazo del uppercase es deliberado y es lo que fija el comentario: un hash
# en mayúsculas es un bug del productor, y normalizarlo en silencio escondería ese
# bug justo en el campo que garantiza la deduplicación. Además, si algún día se
# normalizara, dos productores con criterios distintos podrían converger al mismo
# string y la deduplicación empezaría a fusionar documentos que no son el mismo.
SHA256_HEX = r"^[0-9a-f]{64}$"
Sha256Hex = Annotated[str, Field(pattern=SHA256_HEX)]

# §1.2: techo derivado del límite BSON de 16 MB. Sin él, un texto más largo
# revienta por `DocumentTooLarge` del driver, que llega al cliente como un 500
# sin explicación útil. Con `max_length`, el mismo motor de Pydantic que produce
# el 422 se encarga, sin un `if len(...) > N` que se pueda olvidar en un campo nuevo.
MAX_EXTRACTED_TEXT_CHARS = 10_000_000

MAX_FILENAME_CHARS = 255

COLLECTION_NAME = "pdf_documents"


class ExtractionMethod(StrEnum):
    """Cómo se obtuvo el texto del PDF. Enum cerrado por contrato con el
    orquestador (SPEC.md §1).

    `StrEnum` y no `Enum`: se persiste como string en Mongo y se compara contra el
    string que llega en el body sin tocar `.value` en cada uso.
    """

    PYMUPDF = "pymupdf"
    OCR = "ocr"


EXTRACTION_METHODS = frozenset(method.value for method in ExtractionMethod)


class PdfDocumentFields(BaseModel):
    """Reglas de dominio del documento, sin nada de persistencia.

    Valida lo que el servicio garantiza, con independencia de dónde se guarde. Un
    `BaseModel` puro, así que se puede construir y probar sin Mongo conectado.
    """

    # Un campo que el modelo no conoce es casi siempre un typo del productor
    # (`pdfHash`, `page_counts`). Aceptarlo en silencio es persistir basura que
    # nadie leerá, y el síntoma aparece semanas después en el consumidor.
    model_config = ConfigDict(extra="forbid")

    filename: Annotated[str, Field(min_length=1, max_length=MAX_FILENAME_CHARS)]
    extracted_text: Annotated[str, Field(min_length=1, max_length=MAX_EXTRACTED_TEXT_CHARS)]
    # Enum cerrado (SPEC.md §1). Se tipa como `str` y se valida contra
    # `ExtractionMethod`: un `Literal` daría el mismo 422, pero el enum deja el
    # dominio nombrado y utilizable desde el servicio sin comparar strings sueltos
    # ni repetir la lista en dos sitios.
    extraction_method: str
    page_count: Annotated[int, Field(ge=1)]
    pdf_hash: Sha256Hex
    text_hash: Sha256Hex | None = None

    # D-2: tz-aware por construcción, con `default_factory` y no un valor por
    # defecto. Un naive se interpretaría en la zona local del servidor, y el mismo
    # documento respondería con y sin offset según dónde corra el servicio.
    uploaded_at: Annotated[datetime, Field(default_factory=lambda: datetime.now(UTC))]

    @field_validator("extraction_method")
    @classmethod
    def _validate_extraction_method(cls, value: str) -> str:
        """Enum cerrado. Un método desconocido significa que el orquestador va más
        adelantado que este servicio; aceptarlo guardaría datos que el consumidor no
        sabe interpretar, y el fallo aparecería semanas después, en el consumidor.
        """
        if value not in EXTRACTION_METHODS:
            allowed = ", ".join(sorted(EXTRACTION_METHODS))
            raise ValueError(f"extraction_method debe ser uno de: {allowed}")
        return value

    @field_validator("uploaded_at")
    @classmethod
    def _assume_utc_when_naive(cls, value: datetime) -> datetime:
        """Un naive entrante se interpreta como UTC, no como hora local.

        Descartarlo con un 422 sería más estricto, pero rompería a un productor que
        hoy manda naive. Asumir UTC es la interpretación conservadora: un cliente que
        marca mal la zona queda desplazado, mientras que rechazarlo deja al
        orquestador sin poder persistir nada.
        """
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


class PdfDocument(PdfDocumentFields, Document):
    """El documento de Beanie: las reglas de arriba más el almacenamiento.

    El orden de las bases importa y no es arbitrario: `PdfDocumentFields` va primero
    para que sus campos y validadores se resuelvan antes que los de `Document`.
    """

    class Settings:
        name = COLLECTION_NAME

        # `IndexModel` es el de pymongo y su primer argumento es posicional. No es
        # `beanie.IndexModel`: en Beanie 1.30 ese nombre no se exporta, y el que se
        # usa por debajo es precisamente `pymongo.IndexModel`.
        indexes = [
            # La deduplicación depende por completo de este índice: `unique=True`
            # hace que una inserción duplicada reciba un DuplicateKeyError en vez de
            # crear una segunda entrada idéntica.
            IndexModel(
                [("pdf_hash", 1)],
                unique=True,
                name="uniq_pdf_hash",
            ),
            # US-3 / SC-05: el listado es `uploaded_at DESC, _id DESC`.
            #
            # `_id` va incluido y en **descendente** a propósito. `uploaded_at` tiene
            # granularidad de milisegundos, así que dos documentos subidos en el
            # mismo instante empatan: sin el desempate, el orden puede cambiar entre
            # dos peticiones idénticas y un cliente que pagina vería documentos
            # repetidos o saltados.
            IndexModel(
                [("uploaded_at", -1), ("_id", -1)],
                name="idx_uploaded_at_id_desc",
            ),
        ]
