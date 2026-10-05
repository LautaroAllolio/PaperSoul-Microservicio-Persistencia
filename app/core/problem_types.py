"""URNs de `type` para RFC 9457.

RFC 9457 define `type` como un URI que identifica el tipo de problema. Se usa
URN (`urn:problem:papersoul:<slug>`) y no una URL `https://`: un URN es un URI
válido sin necesidad de que exista un sitio web que lo resuelva.

Estos valores son **API pública**. Los clientes hacen `switch` sobre ellos, así
que cambiarlos es un breaking change aunque el status HTTP no varíe (SPEC.md §7).
"""

from enum import StrEnum

PROBLEM_TYPE_PREFIX = "urn:problem:papersoul"


class ProblemType(StrEnum):
    """Catálogo cerrado de tipos de problema del servicio.

    Los miembros se nombran en SCREAMING_SNAKE_CASE y su valor es el slug en
    snake_case, de modo que el nombre del código y el `type` del JSON se
    corresponden de forma legible.
    """

    MALFORMED_JSON = f"{PROBLEM_TYPE_PREFIX}:malformed_json"
    INVALID_DOCUMENT_ID = f"{PROBLEM_TYPE_PREFIX}:invalid_document_id"
    UNSUPPORTED_MEDIA_TYPE = f"{PROBLEM_TYPE_PREFIX}:unsupported_media_type"
    REQUEST_VALIDATION_FAILED = f"{PROBLEM_TYPE_PREFIX}:request_validation_failed"
    DOCUMENT_NOT_FOUND = f"{PROBLEM_TYPE_PREFIX}:document_not_found"
    DOCUMENT_HASH_CONFLICT = f"{PROBLEM_TYPE_PREFIX}:document_hash_conflict"
    INTERNAL_ERROR = f"{PROBLEM_TYPE_PREFIX}:internal_error"
