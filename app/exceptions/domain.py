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


class UnsupportedMediaTypeException(PaperSoulError):
    """El `Content-Type` declarado no es `application/json`.

    415 y no 400: RFC 9110 §15.5.16 reserva el 415 para "el formato de los datos no
    está soportado por el servidor", mientras que un 400 afirmaría que el servidor
    *entendió* el `Content-Type` y lo rechazó, que es falso.

    La comparación es por media type, no por cabecera entera: `application/json;
    charset=utf-8` es JSON válido, y rechazarlo rompe clientes HTTP bien
    configurados (SPEC.md D-2, SC-21).
    """

    status_code = 415
    problem_type = ProblemType.UNSUPPORTED_MEDIA_TYPE
    title = "Tipo de medio no soportado"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.resource_name = RESOURCE_NAME


class MalformedJsonException(PaperSoulError):
    """El body no es JSON sintácticamente válido.

    400 y no 422: un 422 significa "entendí el JSON y violaste las reglas", y aquí el
    servidor no llegó a entender nada. Un cliente que reintenta ante un 422, pensando
    en un dato corregible, no tiene nada que corregir.

    Se distingue de `RequestValidationError` porque este ultimo llega de Pydantic con
    los campos ya leidos; este llega de `json.JSONDecodeError` (SPEC.md seccion 5.3).
    """

    status_code = 400
    problem_type = ProblemType.MALFORMED_JSON
    title = "JSON malformado"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.resource_name = RESOURCE_NAME


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
