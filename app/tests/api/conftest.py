"""Fixtures de los tests HTTP.

El cliente se monta con un `FakePdfRepository`, **sin lifespan y sin Mongo**.

Eso es posible por dos decisiones de S4, y ambas son la razón de que estos tests
existan antes que Docker:

- `create_app` publica el `Container` en `app.state` al construir la app, no en el
  lifespan. El contenedor es el grafo de dependencias, que no depende de la conexión;
  el lifespan sólo gestiona el recurso (el cliente de Mongo).
- `ASGITransport` no dispara el lifespan, así que una app construida con un fake
  responde sin tocar la base de datos.

Lo que **no** se prueba aquí y sí en `integration/`: que el índice único de Mongo
rechace el duplicado de verdad. Aquí se prueba que la traducción del error da 409.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import uuid4

import httpx
import pytest
from httpx import ASGITransport

from app.main import create_app
from app.tests.fakes import FakePdfRepository

BASE_URL = "http://testserver"
DOCUMENTS_URL = "/api/v1/documents"


def sha256_hex() -> str:
    """64 hex en minúsculas, la forma que exige el contrato para `pdf_hash` (D-1).

    `uuid4().hex` **no** sirve y es una trampa fácil: son 32 caracteres, la mitad de
    lo necesario, y el `pattern` del schema lo rechaza con un 422 que aparece en el
    primer test que postea un documento, muy lejos de la línea que lo causó.
    """
    return uuid4().hex + uuid4().hex


@pytest.fixture
def repository() -> FakePdfRepository:
    """Repositorio en memoria, uno por test.

    Es fixture de función y no de sesión a propósito: si compartiera el estado, un
    test que crea documentos dejaría el siguiente con datos ajenos y el orden de
    ejecución pasaría a importar.
    """
    return FakePdfRepository()


@pytest.fixture
async def client(repository: FakePdfRepository) -> AsyncIterator[httpx.AsyncClient]:
    """Cliente HTTP contra la app completa, montada con el fake."""
    app = create_app(repository=repository)
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as http_client:
        yield http_client


def document_payload(**overrides: Any) -> dict[str, Any]:
    """Body válido y mínimo de `POST /documents`.

    Cada llamada usa un `pdf_hash` distinto, para que un test que crea dos documentos
    no choque con su propia unicidad.
    """
    payload: dict[str, Any] = {
        "filename": "contrato-2026.pdf",
        "extracted_text": "texto extraído del PDF",
        "extraction_method": "pymupdf",
        "page_count": 3,
        "pdf_hash": sha256_hex(),
    }
    payload.update(overrides)
    return payload


async def create_document(client: httpx.AsyncClient, **overrides: Any) -> httpx.Response:
    """Atajo para el caso feliz de `POST`, que media lectura de todos los tests."""
    return await client.post(DOCUMENTS_URL, json=document_payload(**overrides))
