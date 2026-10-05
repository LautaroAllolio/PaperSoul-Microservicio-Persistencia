"""Tests de los URNs de `type` de RFC 9457 (lote S1, tarea 1.2).

Estos URNs son API publica: los clientes hacen `switch` sobre ellos. Si uno cambia,
se rompe el contrato aunque el status HTTP siga igual (SPEC.md §7).
"""

from app.core.problem_types import PROBLEM_TYPE_PREFIX, ProblemType


def test_every_problem_type_has_the_agreed_prefix() -> None:
    """Todos los `type` cuelgan del mismo prefijo.

    Es lo que permite a un cliente reconocer los problemas de este servicio sin
    mantener una lista de slugs.
    """
    for problem_type in ProblemType:
        assert problem_type.value.startswith(PROBLEM_TYPE_PREFIX)


def test_problem_type_values_are_unique() -> None:
    """Dos errores distintos no pueden compartir `type`.

    Si lo hicieran, el cliente no podria distinguirlos y `type` dejaria de ser
    la clave programatica del contrato (SPEC.md §5.3).
    """
    values = [problem_type.value for problem_type in ProblemType]

    assert len(values) == len(set(values))


def test_problem_type_covers_the_full_status_taxonomy() -> None:
    """La taxonomia de SPEC.md §5.3 esta completa: 7 tipos.

    Si se anade un error nuevo y no se actualiza la spec, este test no lo detecta,
    pero obliga a revisar la lista cada vez que se toca.
    """
    expected = {
        "MALFORMED_JSON",
        "INVALID_DOCUMENT_ID",
        "UNSUPPORTED_MEDIA_TYPE",
        "REQUEST_VALIDATION_FAILED",
        "DOCUMENT_NOT_FOUND",
        "DOCUMENT_HASH_CONFLICT",
        "INTERNAL_ERROR",
    }

    assert {member.name for member in ProblemType} == expected


def test_problem_type_values_are_valid_uris() -> None:
    """`type` debe ser un URI (RFC 9457). Se usa URN y no https para no inventar
    un dominio: un URN es un URI valido sin depender de que exista un sitio web.
    """
    for problem_type in ProblemType:
        assert problem_type.value.startswith("urn:problem:papersoul:")
        assert " " not in problem_type.value


def test_slugs_are_snake_case() -> None:
    """Los slugs usan snake_case para mantener el estilo del resto del codigo."""
    for problem_type in ProblemType:
        slug = problem_type.value.removeprefix(f"{PROBLEM_TYPE_PREFIX}:")
        assert slug == slug.lower()
        assert " " not in slug
