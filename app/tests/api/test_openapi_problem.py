"""Tests de la correccion del media type en el OpenAPI (SC-19).

La prueba de que funciona esta en `test_errors_rfc9457.py`, que inspecciona
`/openapi.json` generado de verdad. Este archivo cubre los casos que el documento real
no produce nunca: `parameters` junto a las operaciones, una respuesta sin `content`, un
schema que no es de problema. Son las ramas que decides quitar por "imposibles" y que
un dia rompen el arranque de la app si el generador de FastAPI cambia de forma.
"""

import pytest

from app.api.openapi_problem import (
    _is_problem_schema,
    _iter_responses,
    _move_problem_content,
    _point_at_problem_detail,
)
from app.schemas.problem import PROBLEM_JSON

PROBLEM_REF = {"$ref": "#/components/schemas/ProblemDetail"}
FASTAPI_REF = {"$ref": "#/components/schemas/HTTPValidationError"}
DOCUMENT_REF = {"$ref": "#/components/schemas/DocumentResponse"}


def document_with(**responses: dict) -> dict:
    """Documento minimo con una operacion que tiene las `responses` dadas."""
    return {"paths": {"/x": {"post": {"responses": responses}}}}


def test_problem_content_moves_to_problem_json() -> None:
    schema = {"$ref": "#/components/schemas/ProblemDetail"}
    document = document_with(**{"409": {"content": {"application/json": {"schema": schema}}}})

    _move_problem_content(document)

    content = document["paths"]["/x"]["post"]["responses"]["409"]["content"]
    assert content == {PROBLEM_JSON: {"schema": schema}}
    assert "application/json" not in content


def test_a_response_without_content_is_left_alone() -> None:
    """Un 204 no tiene `content` y no hay que inventarselo.

    Es el caso de `DELETE`, que responde 204 sin cuerpo: anadirle un media type
    documentaria un cuerpo que el servicio no envia.
    """
    document = document_with(**{"204": {"description": "sin cuerpo"}})

    _move_problem_content(document)

    assert document["paths"]["/x"]["post"]["responses"]["204"] == {"description": "sin cuerpo"}


def test_a_successful_response_is_left_as_json() -> None:
    """Un 200 con schema de negocio se queda en `application/json`.

    Es la contraprueba de la regla: si se moviera cualquier schema con `$ref`, un
    `201 DocumentPersistedResponse` acabaria anunciandose como `problem+json`.
    """
    schema = {"$ref": "#/components/schemas/DocumentPersistedResponse"}
    document = document_with(**{"201": {"content": {"application/json": {"schema": schema}}}})

    _move_problem_content(document)

    content = document["paths"]["/x"]["post"]["responses"]["201"]["content"]
    assert content == {"application/json": {"schema": schema}}


def test_fastapi_validation_error_points_at_problem_detail() -> None:
    """El 422 autogenerado por FastAPI se reescribe para que apunte a `ProblemDetail`.

    El cliente generado debe ver `type` e `invalid_params`, no la lista `detail` que
    FastAPI documenta por defecto y este servicio nunca envia.
    """
    content = {"schema": {"$ref": "#/components/schemas/HTTPValidationError"}}

    rewritten = _point_at_problem_detail(content)

    assert rewritten["schema"] == PROBLEM_REF


def test_an_already_declared_problem_json_does_not_duplicate_media_types() -> None:
    """Si el `content` ya declara `problem+json`, se quita el `application/json` rival.

    El repositorio puede declarar el media type a mano en el futuro. Dejar las dos
    entradas haria que un cliente generado acepte `application/json` para el mismo
    body, y volveria a perder el `type` de RFC 9457 justo en el caso que la correccion
    existe para evitar.
    """
    schema = {"$ref": "#/components/schemas/ProblemDetail"}
    already = {
        "content": {
            PROBLEM_JSON: {"schema": schema},
            "application/json": {"schema": schema},
        }
    }
    document = document_with(**{"409": already})

    _move_problem_content(document)

    content = document["paths"]["/x"]["post"]["responses"]["409"]["content"]
    assert content == {PROBLEM_JSON: {"schema": schema}}


def test_parameters_entries_are_skipped() -> None:
    """`paths[x].parameters` es una lista, no una operacion.

    Recorrer `paths` sin mirar el tipo haria que `operation.get` fallara con
    `AttributeError` al generar el documento, y el sintoma seria un 500 al pedir
    `/openapi.json`, no un problema visible en los tests.
    """
    document = {
        "paths": {
            "/x": {
                "parameters": [{"name": "limit", "in": "query"}],
                "get": {"responses": {"200": {"content": {"application/json": {}}}}},
            }
        }
    }

    assert len(list(_iter_responses(document))) == 1


@pytest.mark.parametrize(
    "schema",
    [
        pytest.param(DOCUMENT_REF, id="schema-de-negocio"),
        pytest.param({}, id="schema-vacio"),
        pytest.param("no-es-un-dict", id="schema-no-dict"),
    ],
)
def test_only_problem_schemas_are_recognised(schema: object) -> None:
    assert _is_problem_schema(schema) is False


def test_a_non_dict_response_is_ignored() -> None:
    """Una `response` que no es un dict se descarta en vez de romper la generacion.

    El documento lo genera FastAPI, pero `paths` tambien admite `summary` y
    `description` a nivel de path item, y una futura version del generador podria
    anadir claves mas. Recorrer a ciegas convertiria eso en un `AttributeError` al
    pedir `/openapi.json`.
    """
    document = document_with()
    document["paths"]["/x"]["post"]["responses"] = {"400": "no-es-un-dict"}  # type: ignore[dict-item]

    _move_problem_content(document)


def test_problem_schemas_are_recognised() -> None:
    assert _is_problem_schema(PROBLEM_REF) is True
    assert _is_problem_schema(FASTAPI_REF) is True
