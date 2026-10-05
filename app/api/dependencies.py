"""Proveedores de dependencias de FastAPI, resueltos desde el composition root.

Existe este módulo para que los routers **no construyan** el servicio. Un router que
hace `DocumentService(PdfRepository())` por su cuenta tiene dos grafos de dependencias
en el mismo proceso: uno en producción y otro en cada test, y sólo se prueba el
segundo.

Se declara como dependencia (`Depends`) y no como import directo de `Container` para
que el objeto exacto que llega a los tests sea el mismo que llega en producción: si
se leyera de un global, un test que monta su propia app no vería el cambio.
"""

from typing import Annotated

from fastapi import Depends, Request

from app.core.container import Container
from app.exceptions.domain import UnsupportedMediaTypeException
from app.services.document_service import DocumentService


def get_container(request: Request) -> Container:
    """Devuelve el contenedor que `create_app` publicó en `app.state`.

    Se lee de `request.app.state` y no de un módulo global por dos razones: es
    por-request (dos apps pueden coexistir en el mismo proceso, que es justo lo que
    hace la suite de tests), y hace imposible que un test contamine a otro.
    """
    container: Container = request.app.state.container
    return container


async def require_json_content_type(request: Request) -> None:
    """Comprueba que el body de la peticion se declara como JSON.

    Vive como dependencia y no dentro del handler porque el orden importa: FastAPI lee
    y parsea el body **antes** de invocar el endpoint, asi que un `raise` en el cuerpo
    de la funcion llega tarde para un body que ni siquiera es JSON. La dependencia se
    resuelve antes de la validacion del body y por tanto antes del parseo util.

    Dos reglas, distintas a proposito:

    - Con `Content-Type` presente, se compara el **media type**, no la cadena entera:
      `application/json; charset=utf-8` es valido. Comparar la cabecera completa
      rechaza peticiones correctas y el sintoma es un cliente que "dejo de funcionar".
      Se acepta tambien cualquier `application/*+json` (RFC 6839), que es lo que
      hace FastAPI al decidir si parsea el body.
    - Sin `Content-Type` solo se acepta si no hay body. Un GET o DELETE sin cuerpo no
      lleva la cabecera y es legal; un POST con cuerpo y sin cabecera no dice que
      formato enviar, y adivinar es peor que rechazar (SPEC.md SC-21).

    Para lo segundo se lee el body y no `Content-Length`: un `Content-Length: 0` es
    un valor legitimo que no es el string vacio, y ademas las peticiones con
    `Transfer-Encoding: chunked` no lo declaran. `await request.body()` no consume el
    flujo, Starlette lo cachea y FastAPI leera los mismos bytes de ahi.
    """
    media_type = _media_type(request.headers.get("content-type"))

    if media_type is not None and (
        media_type == "application/json" or media_type.endswith("+json")
    ):
        return

    if media_type is None and not await request.body():
        return

    raise UnsupportedMediaTypeException(
        f"Content-Type {request.headers.get('content-type')!r} no soportado; "
        "se esperaba application/json."
    )


def _media_type(content_type: str | None) -> str | None:
    """Media type en minúsculas y sin sus parámetros (`; charset=utf-8` fuera).

    Devolver `None` cuando no hay cabecera, no una cadena vacía: la ausencia se
    decide aparte en `require_json_content_type` porque sin body es legal.
    """
    if content_type is None:
        return None
    return content_type.split(";", 1)[0].strip().lower()


def get_document_service(
    container: Annotated[Container, Depends(get_container)],
) -> DocumentService:
    """El servicio de documentos, listo para usar.

    Se declara `Annotated[..., Depends(...)]` en el punto de uso y no aquí como
    alias, para que la firma de cada endpoint muestre de un vistazo de dónde sale su
    dependencia.
    """
    return container.document_service
