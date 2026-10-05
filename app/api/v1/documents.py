"""Los 5 endpoints de documentos (SPEC.md §5.2).

Los routers son la **capa 1**: traducen HTTP a llamadas de servicio y de vuelta. No
contienen reglas de negocio y no conocen ni Mongo ni Beanie. Todo lo que hacen es
enrutado, validación de transporte, un `await` al servicio y elegir el status y el
mapeo de la respuesta.

## Lo que este módulo no importa, y por qué

Aquí no hay ni un `import` de `app.repositories` ni de `app.models`, y no es
casualidad: `SPEC.md` §6 lo prohíbe y `tests/test_architecture.py` lo verifica
mecánicamente. Dos decisiones que lo hacen posible:

- **`SHA256_HEX` se importa de `app.schemas.document`**, no de
  `app/models/pdf_document.py` donde se declara. El `pattern` del path param es
  parte del contrato público, así que se lee de donde vive el contrato público. El
  módulo `schemas/document.py` explica por qué lo reexporta.
- **El retorno del listado no se anota.** Antes era
  `page: Page[StoredDocument] = await service.list_documents(...)`, y esa anotación
  obligaba a importar `Page` desde la capa de datos y `StoredDocument` desde la capa
  de persistencia para nombrar un tipo que el servicio ya declara en su firma. mypy
  infiere el tipo exactamente igual y la capa de presentación deja de depender de la
  capa de datos. La lección general: **una anotación puede ser el único motivo de una
  dependencia arquitectónica**, y por eso hay un test que la vigila.

Las respuestas de error **no** se declaran aquí con `@app.exception_handler`: las
traduce un único punto, `api/errors.py`, que lee el status de la excepción de
dominio. Por eso estos handlers no contienen ni un `if` sobre el tipo de error.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, Response, status

from app.api.dependencies import get_document_service, require_json_content_type
from app.api.openapi_problem import problem_responses
from app.schemas.document import (
    SHA256_HEX,
    DocumentCreateRequest,
    DocumentListResponse,
    DocumentPersistedResponse,
    DocumentResponse,
    HashExistsResponse,
)
from app.schemas.mappers import to_list_response, to_persisted_response, to_response
from app.services.document_service import DocumentService

# `status.HTTP_422_UNPROCESSABLE_ENTITY` sigue existiendo pero Starlette lo marca como
# deprecated en favor de `..._CONTENT` (mismo 422, otro nombre). Se usa el nuevo para
# que el warning no ensucie la salida de los tests: un warning que aparece en todos los
# runs acaba normalizado y se deja de leer.
HTTP_422_UNPROCESSABLE_CONTENT = status.HTTP_422_UNPROCESSABLE_CONTENT

router = APIRouter(prefix="/documents", tags=["documents"])

DocumentServiceDep = Annotated[DocumentService, Depends(get_document_service)]

# `Depends` con valor de retorno `None`: no inyecta nada en la firma, solo comprueba.
# Se declara antes que el body a proposito, para que el 415 gane al 422 cuando ambos
# aplicarian (SPEC.md SC-21).
JSONBodyDep = Annotated[None, Depends(require_json_content_type)]


@router.post(
    "",
    response_model=DocumentPersistedResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Persiste un documento ya procesado",
    responses=problem_responses(
        {
            status.HTTP_400_BAD_REQUEST: "El body no es JSON sintacticamente valido",
            status.HTTP_409_CONFLICT: "El pdf_hash ya esta persistido",
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "El body no es JSON",
            HTTP_422_UNPROCESSABLE_CONTENT: "El body no cumple el contrato",
            status.HTTP_500_INTERNAL_SERVER_ERROR: "Error no controlado",
        }
    ),
)
async def create_document(
    _json_body: JSONBodyDep,
    payload: DocumentCreateRequest,
    response: Response,
    service: DocumentServiceDep,
) -> DocumentPersistedResponse:
    """US-2: guarda el documento y confirma con la cabecera `Location`.

    El `Location` no es adorno: es lo que permite al cliente pedir el recurso recien
    creado sin construir la URL a mano, y de paso fija la forma de esa URL en el
    servidor, que es quien debe decidirla.

    `_json_body` no se usa: es la dependencia que devuelve 415 si el `Content-Type` no
    es JSON. Se llama con guion bajo inicial para dejar claro en la firma que es un
    efecto, no un dato de entrada.
    """
    stored = await service.create(payload)
    response.headers["Location"] = f"/api/v1/documents/{stored.id}"

    return to_persisted_response(stored)


# `by-hash` se declara **antes** que `{doc_id}`. No es un conflicto de rutas (dos
# segmentos frente a uno), pero declarar la ruta específica antes de la genérica hace
# explícita la intención y evita que un reordenamiento futuro del archivo produzca un
# 404 confuso si alguien cambia el patrón del identificador.
@router.get(
    "/by-hash/{pdf_hash}",
    response_model=HashExistsResponse,
    summary="Consulta si un pdf_hash ya está persistido",
    responses=problem_responses(
        {
            status.HTTP_400_BAD_REQUEST: "El pdf_hash no tiene el formato exigido",
            status.HTTP_415_UNSUPPORTED_MEDIA_TYPE: "El body no es JSON",
            HTTP_422_UNPROCESSABLE_CONTENT: "El pdf_hash no cumple el patron",
            status.HTTP_500_INTERNAL_SERVER_ERROR: "Error no controlado",
        }
    ),
)
async def check_hash_exists(
    pdf_hash: Annotated[str, Path(pattern=SHA256_HEX, description="SHA-256 hex en minúsculas")],
    service: DocumentServiceDep,
) -> HashExistsResponse:
    """US-1: responde **siempre 200**, exista o no.

    El `pattern` del path param es lo que hace que un hash en mayúsculas dé 422 en vez
    de un `exists: false` (SC-22): si llegara hasta el repositorio, buscaría una
    cadena que por definición no puede existir y devolvería una respuesta
    confundiendo "no lo tengo" con "me lo has mandado mal".
    """
    return await service.find_by_hash(pdf_hash)


@router.get(
    "",
    response_model=DocumentListResponse,
    summary="Lista documentos del más nuevo al más viejo",
    responses=problem_responses(
        {
            HTTP_422_UNPROCESSABLE_CONTENT: "limit u offset fuera de rango",
            status.HTTP_500_INTERNAL_SERVER_ERROR: "Error no controlado",
        }
    ),
)
async def list_documents(
    service: DocumentServiceDep,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DocumentListResponse:
    """US-3: ventana paginada.

    `limit` tiene techo de 100 a propósito: sin él, un `?limit=1000000` convertiría
    el endpoint en una extracción de la colección entera, y el `total` en la única
    promesa de coste acotado.
    """
    page = await service.list_documents(limit=limit, offset=offset)

    return to_list_response(page)


@router.get(
    "/{doc_id}",
    response_model=DocumentResponse,
    summary="Lee un documento por id",
    responses=problem_responses(
        {
            status.HTTP_400_BAD_REQUEST: "El doc_id no es un ObjectId valido",
            status.HTTP_404_NOT_FOUND: "No existe ese documento",
            status.HTTP_500_INTERNAL_SERVER_ERROR: "Error no controlado",
        }
    ),
)
async def get_document(
    doc_id: str,
    service: DocumentServiceDep,
) -> DocumentResponse:
    """SC-06: 404 si no existe, 400 si el id no tiene forma de ObjectId.

    Los dos cortes los hace el repositorio antes de tocar Mongo, así que aquí no hay
    ningún `if` sobre el tipo de error: el handler traduce.
    """
    stored = await service.get_by_id(doc_id)

    return to_response(stored)


@router.delete(
    "/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Elimina un documento por id",
    responses=problem_responses(
        {
            status.HTTP_400_BAD_REQUEST: "El doc_id no es un ObjectId valido",
            status.HTTP_404_NOT_FOUND: "No existe ese documento",
            status.HTTP_500_INTERNAL_SERVER_ERROR: "Error no controlado",
        }
    ),
)
async def delete_document(doc_id: str, service: DocumentServiceDep) -> Response:
    """US-4 y SC-08: 204 sin cuerpo, 404 si no estaba.

    Se declara el retorno como `Response` y no como `None`: con `status_code=204`
    FastAPI ya genera una respuesta vacía, y devolver el `Response` vacío hace
    explícito que no hay nada que serializar en lugar de confiar en ese detalle.
    """
    await service.delete(doc_id)

    return Response(status_code=status.HTTP_204_NO_CONTENT)
