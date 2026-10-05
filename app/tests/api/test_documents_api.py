"""Los 5 endpoints del contrato público (SC-01, SC-02, SC-04..SC-08).

Se prueban contra `FakePdfRepository`, sin Docker: lo que se verifica aquí es el
**transporte** --status, cabeceras, forma del cuerpo, traducción de errores-- que es
justo lo que un test de integración con Mongo no añadiría.

Cada test tiene un comentario con el criterio de aceptación al que corresponde, porque
un `422` sin explicación es el tipo de test que nadie puede arreglar después.
"""

from datetime import UTC, datetime, timedelta

import httpx
import pytest

from app.tests.api.conftest import (
    DOCUMENTS_URL,
    create_document,
    document_payload,
    sha256_hex,
)

# ------------------------------------------------------------ POST /documents


async def test_post_persists_and_answers_201(client: httpx.AsyncClient) -> None:
    """SC-01: 201 con `id` y `status`, más la cabecera `Location`.

    El 201 y no un 200: se creó un recurso y el cliente necesita poder pedirlo a
    partir de la cabecera `Location` sin construir la URL a mano.
    """
    response = await create_document(client)

    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "persisted"
    assert len(body["id"]) == 24


async def test_post_returns_a_location_that_resolves(client: httpx.AsyncClient) -> None:
    """SC-01: la cabecera `Location` tiene que **servir** el documento.

    Comprobar sólo que existe sería insufficient: una `Location` mal construida es el
    fallo clásico de este endpoint, y sólo se detecta si se sigue.
    """
    created = await create_document(client)
    location = created.headers["location"]

    assert location == f"{DOCUMENTS_URL}/{created.json()['id']}"
    followed = await client.get(location)
    assert followed.status_code == 200
    assert followed.json()["id"] == created.json()["id"]


async def test_post_answers_409_on_a_duplicate_hash(client: httpx.AsyncClient) -> None:
    """SC-02 y US-2: el `pdf_hash` repetido es un conflicto, no un error de forma.

    El 409 y no el 400 distingue "no lo has hecho bien" de "ya lo tienes", que es lo
    que permite al orquestador decidir entre reintentar o continuar.
    """
    payload = document_payload()
    assert (await client.post(DOCUMENTS_URL, json=payload)).status_code == 201

    duplicate = await client.post(DOCUMENTS_URL, json=payload)

    assert duplicate.status_code == 409
    assert duplicate.json()["type"] == "urn:problem:papersoul:document-hash-conflict"


async def test_post_does_not_return_the_extracted_text(client: httpx.AsyncClient) -> None:
    """La confirmación del 201 no incluye el texto: puede pesar 10 MB y el cliente
    acaba de enviarlo. Devolverlo duplicaría el tráfico de una respuesta cuyo único
    propósito es confirmar que se guardó.
    """
    response = await create_document(client)

    assert "extracted_text" not in response.json()


# ------------------------------------------ GET /documents/by-hash/{pdf_hash}


async def test_by_hash_answers_200_with_exists_false_when_absent(
    client: httpx.AsyncClient,
) -> None:
    """SC-4 y OT-2: **200**, nunca 404.

    Es la decisión que más ahorra código al orquestador: pregunta "¿lo tengo?" en su
    camino caliente, y un 404 obligaría a capturar una excepción para algo que es una
    respuesta normal.
    """
    response = await client.get(f"{DOCUMENTS_URL}/by-hash/{sha256_hex()}")

    assert response.status_code == 200
    assert response.json() == {"exists": False, "id": None, "uploaded_at": None}


async def test_by_hash_answers_200_with_exists_true_when_present(
    client: httpx.AsyncClient,
) -> None:
    """Cuando existe, el envelope trae `id` y `uploaded_at` para que el orquestador
    pueda ir a la lectura sin una segunda petición.
    """
    pdf_hash = sha256_hex()
    created = await create_document(client, pdf_hash=pdf_hash)

    response = await client.get(f"{DOCUMENTS_URL}/by-hash/{pdf_hash}")

    body = response.json()
    assert response.status_code == 200
    assert body["exists"] is True
    assert body["id"] == created.json()["id"]
    assert body["uploaded_at"] is not None


# ------------------------------------------------------ GET /documents/{doc_id}


async def test_get_by_id_answers_the_document(client: httpx.AsyncClient) -> None:
    created = await create_document(client)

    response = await client.get(f"{DOCUMENTS_URL}/{created.json()['id']}")

    assert response.status_code == 200
    assert response.json()["id"] == created.json()["id"]


async def test_get_by_id_answers_404_when_missing(client: httpx.AsyncClient) -> None:
    """SC-06: id con formato válido pero inexistente es 404.

    Es un caso distinto del 400 y la distinción importa: aquí el servidor entendió la
    petición, simplemente no hay nada detrás.
    """
    response = await client.get(f"{DOCUMENTS_URL}/{sha256_hex()[:24]}")

    assert response.status_code == 404
    assert response.json()["type"] == "urn:problem:papersoul:document-not-found"


async def test_get_by_id_answers_400_for_a_malformed_id(client: httpx.AsyncClient) -> None:
    """SC-06: un id que no puede ser un ObjectId da 400, ni 422 ni 500.

    Ningún payload bien formado produce esta petición, así que es
    *intransmisible* y no *inválida*: es el lado del 400 en el corte de SPEC §5.3.
    """
    response = await client.get(f"{DOCUMENTS_URL}/no-es-un-objectid")

    assert response.status_code == 400
    assert response.json()["type"] == "urn:problem:papersoul:invalid-document-id"


# ----------------------------------------------------------- GET /documents


async def test_list_answers_documents_and_metadata(client: httpx.AsyncClient) -> None:
    """US-3: el listado trae `items` y los metadatos de paginación.

    `total`, `limit` y `offset` van en la respuesta para que el cliente pida la
    ventana siguiente sin tener que deducirlo.
    """
    await create_document(client)

    response = await client.get(DOCUMENTS_URL)

    body = response.json()
    assert response.status_code == 200
    assert body["total"] == 1
    assert body["limit"] == 20
    assert body["offset"] == 0
    assert len(body["items"]) == 1


async def test_list_returns_the_newest_first(client: httpx.AsyncClient) -> None:
    """SC-05: orden `uploaded_at` DESC.

    Se mandan `uploaded_at` explícitos para no depender del reloj: con el
    `default_factory` del modelo, dos insertions en el mismo milisegundo harían el
    test intermitente sin causa aparente.
    """
    base = datetime(2026, 3, 1, 12, 0, tzinfo=UTC)
    await create_document(client, filename="viejo.pdf", uploaded_at=base.isoformat())
    await create_document(
        client,
        filename="nuevo.pdf",
        uploaded_at=(base + timedelta(hours=2)).isoformat(),
    )
    await create_document(
        client,
        filename="medio.pdf",
        uploaded_at=(base + timedelta(hours=1)).isoformat(),
    )

    response = await client.get(DOCUMENTS_URL)

    names = [item["filename"] for item in response.json()["items"]]
    assert names == ["nuevo.pdf", "medio.pdf", "viejo.pdf"]


async def test_list_paginates_without_gaps_or_repeats(client: httpx.AsyncClient) -> None:
    """SC-05: recorrer con `limit`/`offset` no pierde ni duplica documentos.

    Es el caso de uso real. El desempate por `_id` es lo que lo hace fiable cuando dos
    documentos comparten milisegundo, y por eso aquí todos se crean en el mismo
    instante: es el peor caso, no el fácil.
    """
    instant = datetime(2026, 3, 1, 12, 0, tzinfo=UTC).isoformat()
    for index in range(5):
        await create_document(client, filename=f"doc-{index}.pdf", uploaded_at=instant)

    first = (await client.get(DOCUMENTS_URL, params={"limit": 2, "offset": 0})).json()
    second = (await client.get(DOCUMENTS_URL, params={"limit": 2, "offset": 2})).json()
    third = (await client.get(DOCUMENTS_URL, params={"limit": 2, "offset": 4})).json()

    seen = [item["id"] for item in first["items"] + second["items"] + third["items"]]
    assert len(seen) == 5
    assert len(set(seen)) == 5
    assert first["total"] == 5


async def test_list_respects_the_limit(client: httpx.AsyncClient) -> None:
    for _ in range(3):
        await create_document(client)

    response = await client.get(DOCUMENTS_URL, params={"limit": 2})

    body = response.json()
    assert len(body["items"]) == 2
    assert body["total"] == 3
    assert body["limit"] == 2


async def test_list_answers_an_empty_page_when_there_is_nothing(client: httpx.AsyncClient) -> None:
    """Un listado vacío es un 200 con `items: []`, no un 404.

    Un consumidor que lista al arrancar recibe la respuesta vacía con normalidad, y
    un 404 le obligaría a tratarlo como un caso especial.
    """
    response = await client.get(DOCUMENTS_URL)

    assert response.status_code == 200
    assert response.json()["items"] == []


# --------------------------------------------------------------- DELETE


async def test_delete_answers_204_without_a_body(client: httpx.AsyncClient) -> None:
    """SC-08 y US-4: 204 sin cuerpo.

    Un 204 con `{"status": "deleted"}` sería ruido: el status ya lo dice, y devolver
    algo obliga al cliente a distinguir entre "cuerpo vacío" y "cuerpo con estado".
    """
    created = await create_document(client)

    response = await client.delete(f"{DOCUMENTS_URL}/{created.json()['id']}")

    assert response.status_code == 204
    assert response.content == b""


async def test_delete_removes_the_document(client: httpx.AsyncClient) -> None:
    created = await create_document(client)
    document_id = created.json()["id"]

    await client.delete(f"{DOCUMENTS_URL}/{document_id}")

    assert (await client.get(f"{DOCUMENTS_URL}/{document_id}")).status_code == 404


async def test_delete_answers_404_the_second_time(client: httpx.AsyncClient) -> None:
    """SC-08: la segunda llamada da 404.

    Es deliberado y no es idempotencia: el orquestador es la única fuente de verdad y
    necesita distinguir "borré esto" de "no había nada", igual que en un PUT.
    """
    created = await create_document(client)
    url = f"{DOCUMENTS_URL}/{created.json()['id']}"

    assert (await client.delete(url)).status_code == 204
    assert (await client.delete(url)).status_code == 404


async def test_delete_answers_400_for_a_malformed_id(client: httpx.AsyncClient) -> None:
    response = await client.delete(f"{DOCUMENTS_URL}/no-es-un-objectid")

    assert response.status_code == 400
    assert response.json()["type"] == "urn:problem:papersoul:invalid-document-id"


# ------------------------------------------------------ uploaded_at y SC-07


async def test_uploaded_at_is_generated_when_absent(client: httpx.AsyncClient) -> None:
    """El servidor genera `uploaded_at` si el payload no lo trae (SPEC §5.1)."""
    response = await create_document(client)

    assert response.json()["uploaded_at"] is not None


async def test_uploaded_at_keeps_the_utc_offset(client: httpx.AsyncClient) -> None:
    """SC-07: todas las respuestas llevan offset UTC, nunca naive.

    Sin offset, el mismo documento se leería de forma distinta según la zona horaria
    de quien consulta, y comparar fechas entre dos respuestas distintas daría falsos
    negativos.
    """
    pdf_hash = sha256_hex()
    created = await create_document(client, pdf_hash=pdf_hash)
    document_id = created.json()["id"]

    # Las tres rutas que devuelven `uploaded_at`, para comprobar que ninguna lo
    # degrada a naive: el 201, la lectura por id y la entrada del listado.
    by_hash = (await client.get(f"{DOCUMENTS_URL}/by-hash/{pdf_hash}")).json()
    by_id = (await client.get(f"{DOCUMENTS_URL}/{document_id}")).json()
    listed = (await client.get(DOCUMENTS_URL)).json()["items"][0]

    for uploaded_at in (
        created.json()["uploaded_at"],
        by_hash["uploaded_at"],
        by_id["uploaded_at"],
        listed["uploaded_at"],
    ):
        parsed = datetime.fromisoformat(uploaded_at)
        assert parsed.tzinfo is not None
        assert parsed.utcoffset() == UTC.utcoffset(None)


async def test_uploaded_at_accepts_a_client_supplied_value(client: httpx.AsyncClient) -> None:
    """El orquestador puede reintentar una subida con la hora original, y perderla
    sería incorrecto: el valor se respeta tal cual llega.
    """
    moment = "2026-03-01T12:00:00+00:00"

    response = await create_document(client, uploaded_at=moment)

    assert datetime.fromisoformat(response.json()["uploaded_at"]) == datetime.fromisoformat(moment)


# ------------------------------------------------------------------ enrutado


async def test_by_hash_is_not_swallowed_by_the_doc_id_route(
    client: httpx.AsyncClient,
) -> None:
    """`by-hash` se declara **antes** que `{doc_id}` en el router.

    No es un conflicto real de rutas (dos segmentos frente a uno), pero declarar la
    ruta específica primero hace la intención explícita y el test falla ruidosamente
    si alguien invierte el orden al reorderar el archivo.
    """
    pdf_hash = sha256_hex()
    await create_document(client, pdf_hash=pdf_hash)

    response = await client.get(f"{DOCUMENTS_URL}/by-hash/{pdf_hash}")

    assert response.status_code == 200
    assert response.json()["exists"] is True


# -------------------------------------------------------------- health


async def test_health_stays_available(client: httpx.AsyncClient) -> None:
    """La sonda de salud no depende de los documentos ni del prefijo `/api/v1`.

    Un orquestador de contenedores la consulta antes de que exista tráfico, así que
    tiene que responder sin base de datos.
    """
    response = await client.get("/health")

    assert response.status_code == 200


@pytest.mark.parametrize(
    "params",
    [
        {"limit": 0},
        {"limit": 101},
        {"offset": -1},
    ],
)
async def test_list_answers_422_for_out_of_range_pagination(
    client: httpx.AsyncClient, params: dict[str, int]
) -> None:
    """`limit` en 1..100 y `offset` ≥ 0, según el contrato.

    Se rechazan en el borde en vez de recortar en silencio: un `limit: 0` que devolviese
    una lista vacía ocultaría un bug del cliente.
    """
    response = await client.get(DOCUMENTS_URL, params=params)

    assert response.status_code == 422
    assert response.json()["type"] == "urn:problem:papersoul:request-validation-failed"
