"""Handlers globales de error: traducen excepciones a respuestas RFC 9457.

Un único punto de traducción. Los handlers no conocen errores concretos, sólo
leen los atributos que `PaperSoulError` expone, así que añadir un error al
servicio no obliga a tocar este archivo (SPEC.md §6).

`register_exception_handlers` existe como función y no como import-time side
effect: una app de test necesita los handlers sin arrastrar el arranque real.
"""

import logging
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.problem_types import ProblemType
from app.exceptions.base import PaperSoulError
from app.exceptions.domain import MalformedJsonException
from app.schemas.problem import PROBLEM_JSON, InvalidParam, ProblemDetail

logger = logging.getLogger(__name__)

# Texto fijo para el 500. Nunca se sustituye por el mensaje real de la excepción:
# un `RuntimeError("mongodb://user:pass@host:27017")` en el detalle filtra la
# topología interna a cualquiera que reciba la respuesta.
INTERNAL_DETAIL = "Ocurrió un error interno. Inténtalo de nuevo más tarde."


def new_trace_id() -> str:
    """Identificador único de esta ocurrencia concreta.

    Va en la respuesta para que el usuario pueda reportar el fallo y en el log
    del servidor para poder encontrarlo. Sin esta correlación, un 5xx es
    inaccionable desde fuera.
    """
    return str(uuid4())


def problem_response(problem: ProblemDetail) -> JSONResponse:
    """Serializa un ProblemDetail como respuesta HTTP.

    `exclude_none=True` porque en RFC 9457 la ausencia de un miembro significa
    "sin información", y un `null` no es lo mismo: un cliente que comprueba
    `"detail" in body` trata ambos casos de forma distinta.
    """
    return JSONResponse(
        status_code=problem.status,
        content=problem.model_dump(exclude_none=True, mode="json"),
        media_type=PROBLEM_JSON,
    )


async def paper_soul_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Traduce cualquier `PaperSoulError`.

    Genérico a propósito: status, `type` y `title` salen de la propia excepción.
    Por eso un error nuevo no necesita aquí su propio handler.
    """
    assert isinstance(exc, PaperSoulError)
    trace_id = new_trace_id()

    return problem_response(
        ProblemDetail(
            type=exc.problem_type,
            title=exc.title,
            status=exc.status_code,
            detail=exc.detail,
            instance=request.url.path,
            trace_id=trace_id,
        )
    )


async def request_validation_error_handler(request: Request, exc: Exception) -> JSONResponse:
    """Traduce un fallo de validación del body o de los query params a 422.

    Hay una excepción a la regla y es deliberada: un error de tipo `json_invalid` se
    traduce a **400 malformed-json** en vez de a 422. El servidor no llegó a entender
    el body, así que no puede afirmar que violó las reglas. Sin esta bifurcación, un
    cliente que reintenta ante un 422, pensando en un dato corregible, se queda
    reintentando con un JSON que nunca va a parsear (SPEC.md seccion 5.3).
    """
    assert isinstance(exc, RequestValidationError)
    trace_id = new_trace_id()
    errors = exc.errors()

    if any(error["type"] == "json_invalid" for error in errors):
        # Se instancia la excepcion para leer sus atributos en vez de repetir aqui el
        # `type` y el `title`. Duplicarlos seria una segunda fuente de verdad que
        # divergiria en silencio en cuanto el contrato cambiara, y el fallo apareceria
        # como un `type` desconocido en un cliente, no como un test rojo.
        malformed = MalformedJsonException("El cuerpo de la peticion no es JSON valido.")
        return problem_response(
            ProblemDetail(
                type=malformed.problem_type,
                title=malformed.title,
                status=malformed.status_code,
                detail=malformed.detail,
                instance=request.url.path,
                trace_id=trace_id,
            )
        )

    return problem_response(
        ProblemDetail(
            type=ProblemType.REQUEST_VALIDATION_FAILED,
            title="Solicitud no procesable",
            status=422,
            instance=request.url.path,
            trace_id=trace_id,
            invalid_params=[_to_invalid_param(error) for error in errors],
        )
    )


async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:  # noqa: ARG001
    """Red de seguridad para lo que no es `PaperSoulError`.

    Se registra contra `Exception`, así que captura bugs, timeouts de Mongo y
    cualquier cosa inesperada. El log lleva el detalle real porque es interno; la
    respuesta no.

    `exc` no se referencia en el cuerpo: Starlette impone la firma
    `(request, exc)` para todo handler, así que el parámetro es obligatorio
    aunque aquí no haga falta. `logger.exception` ya recoge el traceback desde el
    contexto activo, de modo que leer `exc` no aportaría nada. El `noqa` evita
    silenciar el aviso con un uso artificial del parámetro.
    """
    trace_id = new_trace_id()
    logger.exception(
        "Error no controlado [trace_id=%s] %s %s", trace_id, request.method, request.url.path
    )

    return problem_response(
        ProblemDetail(
            type=ProblemType.INTERNAL_ERROR,
            title="Error interno del servidor",
            status=500,
            detail=INTERNAL_DETAIL,
            instance=request.url.path,
            trace_id=trace_id,
        )
    )


def _to_invalid_param(error: dict[str, Any]) -> InvalidParam:
    """Convierte un error de Pydantic en un miembro extension `invalid_param`.

    Se projecta a tres campos y se descarta el resto. `exc.errors()` incluye `ctx`
    con objetos arbitrarios (incluso `ValueError`, que no es serializable a JSON)
    y `input`, que puede contener el PDF entero que el cliente subir. Volcar el
    error tal cual provocaría un fallo al serializar o una fuga del cuerpo de la
    petición.
    """
    return InvalidParam(
        loc=list(error["loc"]),
        msg=error["msg"],
        type=error.get("type"),
    )


def register_exception_handlers(app: FastAPI) -> None:
    """Conecta los handlers a la app. Un solo punto de entrada, invocado una vez
    desde el lifespan o desde `create_app`.
    """
    app.add_exception_handler(PaperSoulError, paper_soul_error_handler)
    app.add_exception_handler(RequestValidationError, request_validation_error_handler)
    app.add_exception_handler(Exception, unhandled_exception_handler)
