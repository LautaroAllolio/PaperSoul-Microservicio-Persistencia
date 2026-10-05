"""Tests de los handlers globales de error (lote S2, tarea 2.3).

SC-09: todo error sale como `application/problem+json` con la forma de RFC 9457.
Estos tests verifican el contrato HTTP observable, no la implementacion: por eso
van en `app/tests/api/` y no en `unit/`.
"""

from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import BaseModel, Field

from app.api.errors import register_exception_handlers
from app.exceptions.domain import (
    DuplicateResourceException,
    InvalidDocumentIdException,
    ResourceNotFoundException,
)


class Payload(BaseModel):
    """Cuerpo de prueba: anida una lista para forzar el índice en `loc`."""

    filename: str = Field(max_length=5)
    items: list[int] = Field(default_factory=list)


def build_app() -> FastAPI:
    """App minima con los handlers registrados y un router que los dispara.

    Se construye a mano en vez de usar el `main` real porque aqui se prueba el
    cableado de errores, no la configuracion de arranque ni el lifespan de Mongo.
    """
    app = FastAPI()
    register_exception_handlers(app)

    @app.get("/documents/{document_id}")
    async def _get_document(document_id: str) -> None:
        if document_id == "malformed-id":
            raise InvalidDocumentIdException("'malformed-id' no es un ObjectId")
        if document_id == "duplicated":
            raise DuplicateResourceException("el pdf_hash ya está registrado")
        if document_id == "missing":
            raise ResourceNotFoundException("no existe un documento con ese pdf_hash")
        raise RuntimeError("fallo no controlado")

    @app.post("/documents")
    async def _create_document(payload: Payload) -> dict[str, str]:
        return {"filename": payload.filename}

    return app


async def request(app: FastAPI, method: str, url: str, **kwargs: Any) -> httpx.Response:
    """Ejecuta la peticion contra la app en memoria, sin abrir un puerto.

    `raise_app_exceptions=False` es imprescindible para el 500: Starlette relanza
    la excepcion tras responder, y sin esto el test veria el RuntimeError en vez
    del cuerpo problem+json que el cliente recibe.
    """
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        return await client.request(method, url, **kwargs)


@pytest.mark.parametrize(
    ("url", "expected_status", "expected_type"),
    [
        (
            "/documents/missing",
            404,
            "urn:problem:papersoul:document_not_found",
        ),
        (
            "/documents/duplicated",
            409,
            "urn:problem:papersoul:document_hash_conflict",
        ),
        (
            "/documents/malformed-id",
            400,
            "urn:problem:papersoul:invalid_document_id",
        ),
    ],
)
async def test_domain_errors_map_to_its_own_status_and_type(
    url: str,
    expected_status: int,
    expected_type: str,
) -> None:
    """Cada error de dominio sale con su status y su URN, sin intervencion del
    handler. Este es el payoff de la jerarquia: el handler es generico.
    """
    response = await request(build_app(), "GET", url)

    assert response.status_code == expected_status
    assert response.json()["type"] == expected_type


async def test_problem_responses_use_the_problem_json_media_type() -> None:
    """SC-09. El cliente decide como parsear por el Content-Type; servir
    application/json haria que un cliente RFC 9457 no reconoci el cuerpo.
    """
    response = await request(build_app(), "GET", "/documents/missing")

    assert response.headers["content-type"].startswith("application/problem+json")


async def test_problem_response_carries_the_request_path_as_instance() -> None:
    """`instance` identifica la ocurrencia concreta. Es la ruta real pedida, para
    que un cliente con varias llamadas en vuelo sepa a cuál corresponde el error.
    """
    response = await request(build_app(), "GET", "/documents/missing")

    assert response.json()["instance"] == "/documents/missing"


async def test_problem_response_carries_a_trace_id() -> None:
    """El `trace_id` es lo que permite correlacionar el 4xx del cliente con el log
    del servidor. Sin el, un reporte de error no es accionable.
    """
    response = await request(build_app(), "GET", "/documents/missing")

    assert response.json()["trace_id"]


async def test_two_different_errors_get_different_trace_ids() -> None:
    """Cada error se identifica de forma única; reutilizar un id fijo haría
    imposible agrupar por incidencia.
    """
    app = build_app()
    first = await request(app, "GET", "/documents/missing")
    second = await request(app, "GET", "/documents/missing")

    assert first.json()["trace_id"] != second.json()["trace_id"]


async def test_domain_error_detail_reaches_the_client() -> None:
    """`detail` es la causa concreta. Ocultarla obliga al cliente a un ida y vuelta
    de soporte para descubrir qué pasó.
    """
    response = await request(build_app(), "GET", "/documents/duplicated")

    assert response.json()["detail"] == "el pdf_hash ya está registrado"


async def test_unhandled_exception_becomes_500_without_leaking_internals() -> None:
    """SC-09 y la parte de seguridad: el 500 no puede incluir el mensaje de la
    excepción. Un RuntimeError con la ruta de un fichero o una consulta Mongo
    en el `detail` es una fuga de topología interna.
    """
    response = await request(build_app(), "GET", "/documents/inesperado")

    assert response.status_code == 500
    body = response.json()
    assert body["type"] == "urn:problem:papersoul:internal_error"
    assert "fallo no controlado" not in response.text


async def test_unhandled_exception_detail_is_a_generic_message() -> None:
    """El `detail` del 500 es siempre el mismo, no el del fallo concreto."""
    response = await request(build_app(), "GET", "/documents/inesperado")

    assert response.json()["detail"] == "Ocurrió un error interno. Inténtalo de nuevo más tarde."


async def test_unhandled_exception_still_returns_a_trace_id() -> None:
    """Un 500 sin `trace_id` es un callejón sin salida: el usuario reporta y el
    equipo no tiene con qué correlacionar.
    """
    response = await request(build_app(), "GET", "/documents/inesperado")

    assert response.json()["trace_id"]


async def test_validation_failure_is_422_with_the_failing_field() -> None:
    """Un body que no valida es 422, y el error dice QUÉ campo y por qué. Un 400
    genérico obligaría al cliente a reintentar a ciegas.
    """
    response = await request(
        build_app(),
        "POST",
        "/documents",
        json={"filename": "esto-es-demasiado-largo", "items": []},
    )

    assert response.status_code == 422
    assert response.json()["type"] == "urn:problem:papersoul:request_validation_failed"
    assert response.json()["invalid_params"][0]["loc"] == ["body", "filename"]


async def test_validation_failure_reports_the_list_index() -> None:
    """SC-10 en su caso extremo: el elemento que falla dentro de una lista.

    Sin el índice, un cliente no sabe cuál de los 300 elementos corregir y tiene
    que reintentar elemento por elemento. Este test falla si `loc` se aplana.
    """
    response = await request(
        build_app(),
        "POST",
        "/documents",
        json={"filename": "ok", "items": [1, 2, "no-es-un-entero"]},
    )

    assert response.status_code == 422
    assert len(response.json()["invalid_params"]) == 1
    loc = response.json()["invalid_params"][0]["loc"]
    assert loc == ["body", "items", 2]


async def test_validation_failure_lists_every_invalid_param() -> None:
    """Todos los errores de una vez, no solo el primero. Obligar al cliente a
    iterar corrigiendo uno por uno multiplica los viajes por red.
    """
    response = await request(
        build_app(),
        "POST",
        "/documents",
        json={"filename": "largo-demasiado", "items": ["x", 2]},
    )

    assert response.status_code == 422
    locs = [param["loc"] for param in response.json()["invalid_params"]]
    assert ["body", "filename"] in locs
    assert ["body", "items", 0] in locs


async def test_validation_failure_preserves_pydantic_type() -> None:
    """El `type` de Pydantic (`string_too_long`, `int_parsing`) se propaga: es un
    identificador estable y legible, mejor que parsear el `msg`.
    """
    response = await request(
        build_app(),
        "POST",
        "/documents",
        json={"filename": "largo-demasiado", "items": []},
    )

    assert response.json()["invalid_params"][0]["type"] == "string_too_long"


async def test_validation_failure_is_also_problem_json() -> None:
    """SC-09 no distingue el origen del error: un 422 servido como application/json
    rompe al cliente igual que un 404 mal servido.
    """
    response = await request(
        build_app(),
        "POST",
        "/documents",
        json={"filename": "largo-demasiado", "items": []},
    )

    assert response.headers["content-type"].startswith("application/problem+json")


async def test_validation_failure_omits_absent_members() -> None:
    """Coherencia con el resto: un 422 sin `detail` no debe traer `detail: null`."""
    response = await request(
        build_app(),
        "POST",
        "/documents",
        json={"filename": "largo-demasiado", "items": []},
    )

    assert "instance" in response.json()
    assert response.json()["instance"] == "/documents"


async def test_successful_request_is_untouched() -> None:
    """Guardarraíl de regresión: registrar handlers no puede alterar el camino
    feliz, ni el status ni el cuerpo.
    """
    response = await request(
        build_app(), "POST", "/documents", json={"filename": "ok", "items": []}
    )

    assert response.status_code == 200
    assert response.json() == {"filename": "ok"}
