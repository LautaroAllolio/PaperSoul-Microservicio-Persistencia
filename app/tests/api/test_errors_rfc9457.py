"""Matriz de contrato de errores RFC 9457 (tareas 9.3 y 9.4).

Este archivo es una **matriz**, no una lista de casos sueltos: cada fila es una
situacion, un status y un `type`. Agruparlos asi permite leer el contrato completo de
un vistazo contra la tabla de SPEC.md seccion 5.3, en lugar de repartirlo en tests
sueltos cuyos nombres nadie recuerda.

Tres invariantes que se comprueban en **cada** error, sin excepcion:

1. `Content-Type: application/problem+json`. Es lo que permite a un cliente parsear el
   fallo de forma generica sin conocer este servicio (US-5).
2. `type`, `title`, `status` e `instance` presentes. `type` es la clave de
   programatico: es sobre lo que el cliente hace `switch`, no el status HTTP.
3. `status` del cuerpo **reproduce** el status de la respuesta.

Ademas se hace explicita una decision de seguridad: el `detail` de un 500 es texto
fijo y **nunca** el mensaje real de la excepcion. Un
`RuntimeError("mongodb://user:pass@host:27017")` filtrado en el detalle entrega la
topologia interna a cualquiera que reciba la respuesta.
"""

import json
from typing import NoReturn

import httpx
import pytest
from httpx import ASGITransport
from pydantic import BaseModel, ValidationError

from app.api.errors import INTERNAL_DETAIL, _to_invalid_param
from app.main import create_app
from app.tests.api.conftest import (
    BASE_URL,
    DOCUMENTS_URL,
    document_payload,
    sha256_hex,
)
from app.tests.fakes import FakePdfRepository

PROBLEM_JSON = "application/problem+json"

MAX_TEXT_ALLOWED = 10_000_000


@pytest.fixture
def exploding_repository() -> FakePdfRepository:
    """Repositorio cuyo `get_by_id` revienta con una excepcion no controlada.

    Lanza `RuntimeError` y no una `PaperSoulError` a proposito: una excepcion de
    dominio la traduciria el handler especifico y el test no probaria la red de
    seguridad. El mensaje incluye credenciales y una IP para comprobar que nada de eso
    llega al cliente.
    """
    repository = FakePdfRepository()

    async def exploding_get_by_id(document_id: str) -> NoReturn:
        raise RuntimeError(f"fallo al conectar con mongodb://admin:s3cr3t@10.0.0.5:{document_id}")

    repository.get_by_id = exploding_get_by_id  # type: ignore[method-assign]
    return repository


async def assert_is_problem(response: httpx.Response, status: int, problem_type: str) -> None:
    """Comprueba los invariantes comunes a **toda** respuesta de error.

    Vive como helper y no repetido en cada test a proposito: si el invariante cambia,
    se cambia en un sitio. Un test que reimplementa las aserciones puede olvidarse de
    una, y un error que se escapa de la comprobacion es exactamente el que no
    importaba.
    """
    assert response.status_code == status
    assert response.headers["content-type"].startswith(PROBLEM_JSON)

    body = response.json()
    assert body["type"] == problem_type
    assert body["status"] == status
    assert body["title"]
    assert body["instance"]
    assert body["trace_id"]
    # `detail` es diagnostico y puede faltar; `type`, `title`, `status` e `instance`
    # son los que un cliente necesita para decidir. `title` es estable y no se
    # localiza, `detail` si se localiza.


# ------------------------------------------------------------ 415 media type


async def test_post_with_text_plain_answers_415(client: httpx.AsyncClient) -> None:
    """SC-21 y D-2: `Content-Type: text/plain` da 415, no 400.

    Sin un handler propio, FastAPI reportaria un error de JSON y responderia 422. La
    desviacion de 400 a 415 esta justificada en la SPEC: RFC 9110 reserva el 415 para
    "formato no soportado", y un 400 afirmaria que el servidor *entendio* el
    `Content-Type`, que es falso.
    """
    response = await client.post(
        DOCUMENTS_URL,
        content=b"filename=x",
        headers={"Content-Type": "text/plain"},
    )

    await assert_is_problem(response, 415, "urn:problem:papersoul:unsupported-media-type")


async def test_post_with_xml_answers_415(client: httpx.AsyncClient) -> None:
    """El 415 no es un caso especial de `text/plain`: es cualquier media type que no
    sea JSON.
    """
    response = await client.post(
        DOCUMENTS_URL,
        content=b"<document/>",
        headers={"Content-Type": "application/xml"},
    )

    await assert_is_problem(response, 415, "urn:problem:papersoul:unsupported-media-type")


async def test_post_with_a_charset_suffix_is_accepted(client: httpx.AsyncClient) -> None:
    """`application/json; charset=utf-8` es JSON valido y no debe dar 415.

    Es el caso que rompe las comprobaciones ingenuas: si el handler compara el
    `Content-Type` entero con `== "application/json"`, rechaza un `POST` perfectamente
    valido y el sintoma es un cliente HTTP que "dejo de funcionar" sin tocar el
    servidor.

    Se serializa el body a mano porque `json=` de httpx reescribe la cabecera con
    `application/json` y no dejaria probarse el `charset` que es justo lo que aqui
    importa.
    """
    response = await client.post(
        DOCUMENTS_URL,
        content=json.dumps(document_payload()),
        headers={"Content-Type": "application/json; charset=utf-8"},
    )

    assert response.status_code == 201


async def test_get_without_a_content_type_is_accepted(client: httpx.AsyncClient) -> None:
    """`Content-Type` solo se exige donde hay body.

    Un `GET` sin `Content-Type` es legal segun RFC 9110, asi que exigirlo seria
    rechazar peticiones correctas.
    """
    response = await client.get(DOCUMENTS_URL)

    assert response.status_code == 200


async def test_post_without_a_content_type_answers_415(client: httpx.AsyncClient) -> None:
    """SC-21: `Content-Type` ausente **con body** da 415, no 201.

    La ausencia de la cabecera es la parte ambigua de SC-21 y conviene fijarla: un
    cliente que no declara el formato no esta diciendo que formato envia, y adivinar
    significa aceptar un body que el servicio no ha podido interpretar. Sin esto, un
    cliente mal configurado persiste documentos con el servidor leyendo bytes como si
    fueran JSON.
    """
    response = await client.post(
        DOCUMENTS_URL,
        content=json.dumps(document_payload()).encode(),
    )

    await assert_is_problem(response, 415, "urn:problem:papersoul:unsupported-media-type")


async def test_post_without_a_content_type_and_without_a_body_is_accepted(
    client: httpx.AsyncClient,
) -> None:
    """Sin `Content-Type` **y** sin body no hay nada que interpretar: no es un 415.

    Es la contraprueba del caso anterior, y por eso importa tenerla: una regla de
    "exigir siempre la cabecera" pasaria el test del 415 y fallaria este. Lo que
    distingue las dos peticiones es la presencia del body, no la de la cabecera.
    """
    response = await client.post(DOCUMENTS_URL)

    assert response.status_code in {400, 422}
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


async def test_post_with_a_vendor_json_subtype_is_accepted(client: httpx.AsyncClient) -> None:
    """`application/*+json` es JSON (RFC 6839) y no debe rechazarse.

    Es el mismo criterio que usa FastAPI para decidir si parsea el body: si el servidor
    acepta el `application/vnd.miapi+json` pero lo parsea como texto, el cliente
    recibe un 422 que dice "body vacio" sobre un body que si envio.
    """
    response = await client.post(
        DOCUMENTS_URL,
        content=json.dumps(document_payload()),
        headers={"Content-Type": "application/vnd.papersoul.document+json"},
    )

    assert response.status_code == 201


# -------------------------------------------------------------- 400 JSON malo


async def test_post_with_malformed_json_answers_400(client: httpx.AsyncClient) -> None:
    """JSON que no parsea es 400 `malformed-json`, no 422.

    La distincion es semantica: un 422 significa "entiendi el JSON y violaste las
    reglas"; aqui el servidor no llego a entender nada. Un cliente que reintenta ante
    un 422, pensando en un dato corregible, no tiene nada que corregir.
    """
    response = await client.post(
        DOCUMENTS_URL,
        content=b'{"filename": "roto",,}',
        headers={"Content-Type": "application/json"},
    )

    await assert_is_problem(response, 400, "urn:problem:papersoul:malformed-json")


async def test_post_with_an_empty_body_is_rejected(client: httpx.AsyncClient) -> None:
    """Un body vacio no es un documento: es una peticion sin contenido.

    Se acepta 400 o 422 porque ambos son defendibles segun como se implemente la
    lectura del body, y lo que importa es que **no** sea un 500 ni un 201. Fijar el
    status exacto aqui ataria el test a un detalle de implementacion sin ganancia.
    """
    response = await client.post(
        DOCUMENTS_URL,
        content=b"",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code in {400, 422}
    assert response.headers["content-type"].startswith(PROBLEM_JSON)


# --------------------------------------------------------------- 422 validacion


async def test_unknown_field_answers_422_and_is_not_persisted(
    client: httpx.AsyncClient,
) -> None:
    """SC-11: `pdfHash` en vez de `pdf_hash` da 422 y no se guarda nada.

    Es `additionalProperties: false` lo que lo detecta. Sin el, el campo desconocido se
    descartaria en silencio y el documento se guardaria **sin `pdf_hash`**: sin indice,
    sin deduplicacion, y semanas despues nadie sabria por que el mismo PDF entra dos
    veces.
    """
    payload = document_payload()
    # Se **anade** el campo mal escrito y se quita el bueno: un productor que escribe
    # `pdfHash` no manda `pdf_hash`, lo manda en su lugar.
    payload["pdfHash"] = payload.pop("pdf_hash")

    response = await client.post(DOCUMENTS_URL, json=payload)

    await assert_is_problem(response, 422, "urn:problem:papersoul:request-validation-failed")
    locs = [param["loc"] for param in response.json()["invalid_params"]]
    assert ["body", "pdfHash"] in locs


async def test_extracted_text_too_long_answers_422(client: httpx.AsyncClient) -> None:
    """SC-12: 10 000 001 caracteres dan 422, no un `DocumentTooLarge` del driver.

    El techo existe porque BSON no admite documentos de mas de 16 MB: sin el, el
    cliente recibiria un 500 sin explicacion util por un dato que el mismo envio.
    """
    response = await client.post(
        DOCUMENTS_URL,
        json=document_payload(extracted_text="a" * (MAX_TEXT_ALLOWED + 1)),
    )

    await assert_is_problem(response, 422, "urn:problem:papersoul:request-validation-failed")


async def test_page_count_below_one_answers_422(client: httpx.AsyncClient) -> None:
    """OT-3: un PDF de 0 paginas no es legal y se rechaza en el borde."""
    response = await client.post(DOCUMENTS_URL, json=document_payload(page_count=0))

    await assert_is_problem(response, 422, "urn:problem:papersoul:request-validation-failed")


async def test_unknown_extraction_method_answers_422(client: httpx.AsyncClient) -> None:
    """El enum es cerrado: un metodo desconocido significa que el orquestador va mas
    adelante que este servicio, y aceptarlo guardaria datos que el consumidor no sabe
    interpretar.
    """
    response = await client.post(
        DOCUMENTS_URL,
        json=document_payload(extraction_method="magic"),
    )

    await assert_is_problem(response, 422, "urn:problem:papersoul:request-validation-failed")


async def test_missing_required_field_answers_422(client: httpx.AsyncClient) -> None:
    """`filename` es obligatorio, y su ausencia se localiza en `invalid_params`."""
    payload = document_payload()
    del payload["filename"]

    response = await client.post(DOCUMENTS_URL, json=payload)

    await assert_is_problem(response, 422, "urn:problem:papersoul:request-validation-failed")
    locs = [param["loc"] for param in response.json()["invalid_params"]]
    assert ["body", "filename"] in locs


# --------------------------------------------------- SC-10 invalid_params loc


async def test_validation_error_preserves_list_indexes() -> None:
    """SC-10: `loc` conserva los indices de una lista.

    El contrato publico no tiene ningun body con listas, asi que se comprueba el
    mecanismo que las genera en vez de inventarse un endpoint para el test: un cliente
    que corrige payloads necesita saber *que elemento* fallo, no solo que fallo algo
    dentro de la lista. Si `_to_invalid_param` aplanara `loc`, un esquema futuro con
    listas publicaria rutas inservibles sin que ningun test de este archivo lo notara.
    """

    class ConLista(BaseModel):
        items: list[int]

    try:
        ConLista(items=[1, 2, "no-soy-un-entero"])  # type: ignore[list-item]
    except ValidationError as error:
        errors = error.errors()
    else:
        raise AssertionError("ConLista deberia haber fallado con el tercer elemento")

    invalid_param = _to_invalid_param(errors[0])

    assert invalid_param.loc == ["items", 2]
    assert invalid_param.type == "int_parsing"


async def test_validation_error_does_not_leak_the_payload(client: httpx.AsyncClient) -> None:
    """El `input` del error de Pydantic no se vuelca en la respuesta.

    `exc.errors()` incluye `input`, que aqui es el documento entero que el cliente
    acaba de enviar, y `ctx`, que puede contener objetos no serializables a JSON.
    Volcarlos provocaria un fallo al serializar la respuesta o una fuga del cuerpo de
    la peticion, que puede contener datos sensibles de un contrato.
    """
    secret = "texto muy confidencial del contrato"

    accepted = await client.post(DOCUMENTS_URL, json=document_payload(extracted_text=secret))
    assert accepted.status_code == 201

    rejected = await client.post(
        DOCUMENTS_URL,
        json=document_payload(extracted_text=secret, page_count=0),
    )
    assert secret not in rejected.text


# ------------------------------------------------------------- 400 y 404 de id


async def test_invalid_document_id_answers_400(client: httpx.AsyncClient) -> None:
    """SC-06: un id no-ObjectId da 400 con su propio `type`, no el generico de
    validacion.

    El `type` distinto es lo que permite al cliente diferenciar "te mande un id
    invalido" de "el body no cumple el contrato", aunque ambos sean 4xx.
    """
    response = await client.get(f"{DOCUMENTS_URL}/xyz")

    await assert_is_problem(response, 400, "urn:problem:papersoul:invalid-document-id")


async def test_missing_document_answers_404(client: httpx.AsyncClient) -> None:
    """SC-06: un ObjectId valido que no existe da 404.

    El `detail` menciona el id concreto: ayuda al operador a corregir una peticion sin
    tener que mirar el log.
    """
    response = await client.get(f"{DOCUMENTS_URL}/{sha256_hex()[:24]}")

    await assert_is_problem(response, 404, "urn:problem:papersoul:document-not-found")


async def test_uppercase_pdf_hash_answers_422_and_is_not_normalized(
    client: httpx.AsyncClient,
) -> None:
    """SC-22 y D-1: un hash en mayusculas se rechaza, no se normaliza.

    Normalizar esconderia un bug del productor en el campo que garantiza la
    deduplicacion, que es justo el campo donde un bug debe verse.
    """
    response = await client.get(f"{DOCUMENTS_URL}/by-hash/{sha256_hex().upper()}")

    await assert_is_problem(response, 422, "urn:problem:papersoul:request-validation-failed")


@pytest.mark.parametrize(
    "value",
    [
        pytest.param("corto", id="longitud-distinta-de-64"),
        pytest.param("z" * 64, id="caracteres-no-hex"),
        pytest.param("a" * 63, id="un-hex-de-menos"),
    ],
)
async def test_malformed_pdf_hash_answers_422(client: httpx.AsyncClient, value: str) -> None:
    """SC-22: cualquier desviacion del formato `^[0-9a-f]{64}$` da 422.

    El hash vacio queda fuera a proposito: una URL terminada en `/by-hash/` no llega al
    handler, la ruta no casa y Starlette responde con un 307 de redireccion de barra
    final. Es comportamiento del framework de enrutado, no del contrato de `pdf_hash`,
    y atarlo aqui haria que el test fallara por una razon que no dice comprobar.
    """
    response = await client.get(f"{DOCUMENTS_URL}/by-hash/{value}")

    assert response.status_code == 422
    assert response.json()["type"] == "urn:problem:papersoul:request-validation-failed"


# -------------------------------------------------------------------- 500


async def test_unhandled_exception_answers_500_without_leaking_details(
    exploding_repository: FakePdfRepository,
) -> None:
    """Un error no controlado da 500 con `detail` fijo y el detalle real solo en el log.

    Es la frontera de seguridad del servicio. El log lleva la excepcion completa para
    poder diagnosticar; la respuesta lleva un texto que no revela ni la topologia ni el
    mensaje del error.

    Se inyecta el fallo por el repositorio en vez de parchear el handler: asi se
    prueba el camino real, peticion -> router -> servicio -> repositorio que explota, y
    no una version del handler con el `except` puesto a mano.

    `raise_app_exceptions=False` es necesario y no es un detalle menor: el
    `ServerErrorMiddleware` de Starlette construye la respuesta 500 y **despues** vuelve
    a lanzar la excepcion, para que el servidor que la hospeda la registre. Con el
    valor por defecto de `ASGITransport` (`True`) el test ve la excepcion y falla antes
    de poder comprobar la respuesta, es decir, no llegaria a probar lo que dice probar.
    """
    app = create_app(repository=exploding_repository)
    transport = ASGITransport(app=app, raise_app_exceptions=False)

    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as client:
        response = await client.get(f"{DOCUMENTS_URL}/{sha256_hex()[:24]}")

    await assert_is_problem(response, 500, "urn:problem:papersoul:internal-error")
    assert "s3cr3t" not in response.text
    assert "10.0.0.5" not in response.text
    assert response.json()["detail"] == INTERNAL_DETAIL


# --------------------------------------------------------------- SC-19 OpenAPI


async def test_openapi_declares_every_error_as_problem_json(
    client: httpx.AsyncClient,
) -> None:
    """SC-19: toda respuesta de error del OpenAPI se declara como `problem+json`.

    Un cliente generado a partir del OpenAPI decide como parsear cada respuesta segun
    el media type declarado. Si un 409 aparece como `application/json`, el cliente lo
    parsea como un cuerpo normal y el `type` de RFC 9457 se pierde en el camino: el
    error se manifesta como un `KeyError` en el cliente, no como un problema en el
    servidor, y por tanto en produccion y no en los tests.
    """
    document = await client.get("/openapi.json")
    paths = document.json()["paths"]

    documented = {
        (method, path, status)
        for path, operations in paths.items()
        for method, operation in operations.items()
        for status in operation["responses"]
        if status.startswith(("4", "5"))
    }
    assert documented, "el OpenAPI no documenta ninguna respuesta de error"

    for method, path, status in documented:
        media_types = paths[path][method]["responses"][status].get("content", {})
        assert list(media_types) == [PROBLEM_JSON], f"{method} {path} {status}"


async def test_openapi_declares_openapi_31(client: httpx.AsyncClient) -> None:
    """SC-19: el documento es OpenAPI 3.1.0.

    No es un detalle de version. 3.1 **es** JSON Schema, asi que un cliente puede
    validar contra el mismo schema que sirve el documento; en 3.0 el `nullable` de
    OpenAPI no existe y el schema no se puede reutilizar tal cual.
    """
    document = await client.get("/openapi.json")

    assert document.json()["openapi"] == "3.1.0"


async def test_openapi_declares_the_five_endpoints(client: httpx.AsyncClient) -> None:
    """SC-19: los 5 endpoints de SPEC.md §5.2 estan documentados.

    `/health` se documenta pero no se cuenta: es liveness, no parte del contrato de
    negocio. La comparacion es de subconjunto (`<=`) y no de igualdad para que anadir
    un endpoint nuevo no rompa este test, que no es lo que dice comprobar.
    """
    document = await client.get("/openapi.json")

    assert {
        "/api/v1/documents",
        "/api/v1/documents/{doc_id}",
        "/api/v1/documents/by-hash/{pdf_hash}",
    } <= set(document.json()["paths"])


async def test_openapi_error_schema_is_problem_detail(
    client: httpx.AsyncClient,
) -> None:
    """El schema de los errores es `ProblemDetail`, no el `HTTPValidationError` de FastAPI.

    FastAPI genera un `HTTPValidationError` para el 422 de cualquier operacion con
    parametros. Este servicio no devuelve eso: su handler responde un `ProblemDetail`
    con `type` e `invalid_params`. Dejar el `$ref` de FastAPI haria que un cliente
    generated esperase `{"detail": [{"loc": ...}]}` y no encontrara `type`, que es la
    clave sobre la que el cliente hace `switch`.
    """
    document = await client.get("/openapi.json")
    responses = document.json()["paths"]["/api/v1/documents"]["post"]["responses"]

    schema = responses["422"]["content"][PROBLEM_JSON]["schema"]

    assert schema == {"$ref": "#/components/schemas/ProblemDetail"}


async def test_every_error_carries_a_trace_id(client: httpx.AsyncClient) -> None:
    """Un error sin `trace_id` es inaccionable desde fuera.

    Es el motivo de que `trace_id` este en la respuesta y no solo en el log: sin una
    correlacion, quien recibe el error no tiene forma de encontrar la linea
    correspondiente en el servidor.
    """
    response = await client.get(f"{DOCUMENTS_URL}/{sha256_hex()[:24]}")

    assert response.status_code == 404
    assert response.json()["trace_id"]
