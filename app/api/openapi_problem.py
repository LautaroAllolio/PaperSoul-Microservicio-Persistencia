"""Documentacion de las respuestas de error en el OpenAPI (SC-19).

SC-19 exige que `/openapi.json` declare las respuestas de error como
`application/problem+json`. Escribir el media type a mano en cada endpoint funciona
hasta el primer endpoint nuevo, en el que alguien omite el `content` y el cliente
generado parsea el problema como un cuerpo normal. El sintoma aparece en el cliente,
lejos del servidor.

Hay un limite de FastAPI que obliga a este diseno: el `model` de una respuesta
adicional se documenta siempre bajo el media type de la clase de respuesta, que es
`application/json`. No hay forma de declararlo como `application/problem+json` desde
el decorador, y comparar la cabecera entera tampoco es la solucion.

Por eso el trabajo se parte en dos: `problem_responses` declara el modelo (que es lo
que registra `ProblemDetail` en `components.schemas`) y `use_problem_media_types`
corrige despues el media type de las respuestas cuyo schema sea ese modelo. La
correccion se identifica por el `$ref`, no por una lista de endpoints mantenida a mano:
un endpoint nuevo que use `problem_responses` queda bien sin que nadie lo recuerde.
"""

from collections.abc import Iterator, Mapping
from typing import Any

from fastapi import FastAPI

from app.schemas.problem import PROBLEM_JSON, ProblemDetail

APPLICATION_JSON = "application/json"

# `HTTPValidationError` se incluye a proposito: FastAPI lo genera solo para el 422 de
# las operaciones con parametros, y ese 422 lo produce **nuestro** handler, que
# responde `ProblemDetail`. Si solo se moviera `ProblemDetail`, quedaria un 422
# documentado con el schema de FastAPI y en `application/json`, es decir, el
# OpenAPI describiria un cuerpo de error que el servicio nunca envia.
PROBLEM_SCHEMA_NAMES = (ProblemDetail.__name__, "HTTPValidationError")


def problem_responses(by_status: Mapping[int, str]) -> dict[int | str, dict[str, Any]]:
    """Mapa `responses` de FastAPI con `ProblemDetail` en cada status indicado.

    El uso es con un diccionario de status a descripcion:

        responses=problem_responses({409: "El pdf_hash ya esta persistido"})

    Se acepta un `Mapping` y no pares sueltos (`**`) porque las claves de `**` deben
    ser cadenas y los status son enteros: `problem_responses(**{409: "..."})` falla
    con `TypeError: keywords must be strings` al importar el modulo.

    Se declara `model` y no `content` a proposito: es `model` lo que hace que
    FastAPI anada `ProblemDetail` a `components.schemas`. Un `$ref` escrito a mano
    apuntaria a un schema que no existe en el documento.
    """
    return {
        status_code: {"description": description, "model": ProblemDetail}
        for status_code, description in by_status.items()
    }


def use_problem_media_types(app: FastAPI) -> None:
    """Hace que `/openapi.json` sirva los errores como `application/problem+json`.

    Sustituye a `app.openapi` por una version que, tras generar el documento, mueve
    cada respuesta cuyo schema sea `ProblemDetail` del media type `application/json` a
    `application/problem+json`. Se hace aqui y no en cada endpoint porque el
    `application/json` lo impone el generador de FastAPI, no la declaracion del
    endpoint: corregirlo en el decorador no seria posible.
    """
    generate_openapi = app.openapi

    def openapi_with_problem_media_type() -> dict[str, Any]:
        document = generate_openapi()
        _move_problem_content(document)
        return document

    app.openapi = openapi_with_problem_media_type  # type: ignore[method-assign]


def _move_problem_content(document: dict[str, Any]) -> None:
    for response in _iter_responses(document):
        _move_response_content(response)


def _iter_responses(document: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Cada objeto `responses` de cada operacion del documento.

    Se recorre `paths` de forma generica en lugar de listar las cinco rutas: un
    endpoint nuevo debe quedar cubierto por la misma regla que los otros cinco.
    """
    for path_item in document.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict):
                continue
            yield from operation.get("responses", {}).values()


def _move_response_content(response: Any) -> None:
    if not isinstance(response, dict):
        return

    content = response.get("content", {})
    json_content = content.get(APPLICATION_JSON)

    if json_content is None:
        return

    if PROBLEM_JSON in content:
        # Ya esta declarado como `problem+json`, pero `application/json` sigue
        # anunciando el mismo body como un JSON normal. Se quita igualmente: dos
        # media types para el mismo body hacen que un cliente generado acepte el
        # equivocado.
        del content[APPLICATION_JSON]
        return

    problem_content = json_content

    if not _is_problem_schema(problem_content.get("schema", {})):
        return

    del content[APPLICATION_JSON]
    problem_content = _point_at_problem_detail(problem_content)
    content[PROBLEM_JSON] = problem_content


def _point_at_problem_detail(content: dict[str, Any]) -> dict[str, Any]:
    """Sustituye el schema de FastAPI por el de `ProblemDetail`.

    El 422 que FastAPI genera solo documenta `HTTPValidationError`, que no es lo que
    este servicio devuelve: su handler responde un `ProblemDetail`. Dejar el `$ref`
    original haria que un cliente generado creyera que recibe
    `{"detail": [{"loc": ...}]}` en vez de `{"type": ..., "invalid_params": [...]}`,
    que es el cambio real del contrato.
    """
    reference = content.get("schema", {}).get("$ref", "")
    if not reference.endswith("/HTTPValidationError"):
        return content

    return {**content, "schema": {"$ref": f"#/components/schemas/{ProblemDetail.__name__}"}}


def _is_problem_schema(schema: Any) -> bool:
    if not isinstance(schema, dict):
        return False
    return any(schema.get("$ref", "").endswith(f"/{name}") for name in PROBLEM_SCHEMA_NAMES)
