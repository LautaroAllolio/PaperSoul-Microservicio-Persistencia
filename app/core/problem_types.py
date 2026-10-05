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

    Los miembros se nombran en SCREAMING_SNAKE_CASE y su valor es el slug con
    guiones, tal y como los fija SPEC.md §5.3. Los clientes hacen `switch` sobre
    estos strings, así que el separador no es un detalle de estilo: el código usa
    guiones porque ése es el contrato aprobado.
    """

    MALFORMED_JSON = f"{PROBLEM_TYPE_PREFIX}:malformed-json"
    INVALID_DOCUMENT_ID = f"{PROBLEM_TYPE_PREFIX}:invalid-document-id"
    UNSUPPORTED_MEDIA_TYPE = f"{PROBLEM_TYPE_PREFIX}:unsupported-media-type"
    REQUEST_VALIDATION_FAILED = f"{PROBLEM_TYPE_PREFIX}:request-validation-failed"
    DOCUMENT_NOT_FOUND = f"{PROBLEM_TYPE_PREFIX}:document-not-found"
    DOCUMENT_HASH_CONFLICT = f"{PROBLEM_TYPE_PREFIX}:document-hash-conflict"
    INTERNAL_ERROR = f"{PROBLEM_TYPE_PREFIX}:internal-error"
