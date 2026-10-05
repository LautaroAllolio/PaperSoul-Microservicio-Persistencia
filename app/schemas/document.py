"""DTOs de documentos: el contrato público hacia el orquestador.

Viven fuera de `api/` porque son el contrato de la API, no un detalle del
transporte. El servicio los usa para tipar sus retornos sin importar FastAPI
(SPEC.md §4).

Cada DTO declara `extra="forbid"` de forma explícita, porque en el OpenAPI el
contrato pactado con `Extraer` es `additionalProperties: false`. Un `pdfHash`
mal escrito debe recibir un 422 que lo diga, no guardarse como un campo
desconocido que nadie leerá.

## `SHA256_HEX` se importa aquí a propósito

Las constantes de contrato (`SHA256_HEX`, `MAX_EXTRACTED_TEXT_CHARS`,
`MAX_FILENAME_CHARS`) se declaran en `app/models/pdf_document.py`, que es donde vive
la regla de dominio que las produce, y se importan hacia aquí. `SHA256_HEX` además
se **reexporta** desde este módulo, porque el router lo necesita para declarar el
`pattern` del path param de `by-hash` y no puede ir a `models/` a buscarlo:
`SPEC.md` §6 prohíbe de forma explícita `api -> models`.

El path param y el campo del body deben compartir la misma expresión regular. Si cada
uno declarara la suya, un hash que pasara por el body podría ser rechazado en el path
o al revés, y el fallo aparecería sólo para un subconjunto de hashes.
"""

from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.pdf_document import (
    MAX_EXTRACTED_TEXT_CHARS,
    MAX_FILENAME_CHARS,
    SHA256_HEX,
)

ExtractionMethodLiteral = Literal["pymupdf", "ocr"]


class DocumentCreateRequest(BaseModel):
    """Body de `POST /documents`: lo que declara haber hecho el orquestador."""

    model_config = ConfigDict(extra="forbid")

    filename: Annotated[str, Field(min_length=1, max_length=MAX_FILENAME_CHARS)]
    extracted_text: Annotated[str, Field(min_length=1, max_length=MAX_EXTRACTED_TEXT_CHARS)]
    extraction_method: ExtractionMethodLiteral
    page_count: Annotated[int, Field(ge=1)]
    # `Literal` en vez de `str` con validador: el esquema OpenAPI sale con el enum
    # restringido sin código extra, y es el mismo tipo que FastAPI publica.
    pdf_hash: Annotated[str, Field(pattern=SHA256_HEX)]
    text_hash: Annotated[str, Field(pattern=SHA256_HEX)] | None = None
    # Opcional a propósito. El servidor lo genera si no viene, pero se acepta si
    # viene: el orquestador puede estar reintentando una subida con la hora
    # original y perderla sería incorrecto.
    uploaded_at: datetime | None = None


class DocumentResponse(BaseModel):
    """Representación de un documento hacia el exterior."""

    model_config = ConfigDict(extra="forbid")

    id: str
    filename: str
    extracted_text: str
    extraction_method: ExtractionMethodLiteral
    page_count: int
    pdf_hash: str
    text_hash: str | None = None
    # D-2 y SC-07: siempre con offset UTC. El cliente se crea con `tz_aware=True`,
    # de modo que esto llega con `+00:00` sin que el mapper tenga que intervenir.
    uploaded_at: datetime


class DocumentPersistedResponse(BaseModel):
    """Respuesta del 201: la confirmación mínima, no el documento entero.

    No devuelve `extracted_text` a propósito. Puede tener 10 MB, y el orquestador
    acaba de enviarlo: devolvérselo duplica el tráfico de una respuesta cuyo único
    propósito es confirmar que se guardó.
    """

    id: str
    # `Literal` y no `str`: el status es una **constante del contrato de
    # transporte**, no un estado persistido (SPEC.md §1). Al tiparlo como literal, un
    # cambio accidental de valor rompe el test en vez de colarse en producción como un
    # string nuevo que el orquestador no reconoce.
    #
    # `pdf_hash` y `uploaded_at` se incluyen además de los dos campos que exige
    # SPEC.md §5.1: son aditivos y ahorran al orquestador una segunda petición para
    # confirmar qué se guardó. No rompen a un cliente que lea `id` y `status`.
    status: Literal["persisted"] = "persisted"
    pdf_hash: str
    uploaded_at: datetime


class HashExistsResponse(BaseModel):
    """Respuesta de `GET /by-hash/{pdf_hash}`: **siempre 200**, exista o no.

    `exists: false` con `id` y `uploaded_at` en `null` es una respuesta válida, y no
    un error: la pregunta "ya está subido este PDF" tiene respuesta "no" con la
    misma naturalidad que "sí".
    """

    exists: bool
    id: str | None = None
    uploaded_at: datetime | None = None


class DocumentListResponse(BaseModel):
    """Respuesta del listado paginado."""

    items: list[DocumentResponse]
    total: int
    limit: int
    offset: int
