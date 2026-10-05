"""Errores de dominio concretos del servicio de persistencia.

Cada clase aporta el status, el `type` URN y el `title` de su caso. El handler
HTTP no los conoce: sólo lee estos atributos a través de `PaperSoulError`
(SPEC.md §6).
"""

from app.core.problem_types import ProblemType
from app.exceptions.base import PaperSoulError

RESOURCE_NAME = "document"


class ResourceNotFoundException(PaperSoulError):
    """El recurso no existe.

    404 y no 400: la petición se entiende y el identificador está bien formado,
    simplemente no hay nada detrás. Un identificador con formato *imposible* es un
    400 (ver `InvalidDocumentIdException`).
    """

    status_code = 404
    problem_type = ProblemType.DOCUMENT_NOT_FOUND
    title = "Documento no encontrado"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.resource_name = RESOURCE_NAME


class DuplicateResourceException(PaperSoulError):
    """Violación de unicidad.

    409 y no 400: la petición es perfectamente válida, pero el estado actual la
    rechaza. Es la respuesta al `pdf_hash` repetido, y existe para que el
    orquestador pueda distinguir "no lo has hecho bien" de "ya lo tienes" sin
    leer el `detail`.
    """

    status_code = 409
    problem_type = ProblemType.DOCUMENT_HASH_CONFLICT
    title = "Conflicto de documento"

    def __init__(self, detail: str, resource_name: str = RESOURCE_NAME) -> None:
        super().__init__(detail)
        self.resource_name = resource_name


class InvalidDocumentIdException(PaperSoulError):
    """El identificador no tiene el formato de un ObjectId de MongoDB.

    400 y no 422: ninguna petición bien formada lo produce, así que el corte
    400/422 de SPEC.md §5.3 lo sitúa del lado de la petición *intransmisible*.
    Tampoco es un 404: el servicio no llegó a consultar nada.
    """

    status_code = 400
    problem_type = ProblemType.INVALID_DOCUMENT_ID
    title = "Identificador de documento inválido"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.resource_name = RESOURCE_NAME
