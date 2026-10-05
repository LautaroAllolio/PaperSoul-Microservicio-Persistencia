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
from app.services.document_service import DocumentService


def get_container(request: Request) -> Container:
    """Devuelve el contenedor que `create_app` publicó en `app.state`.

    Se lee de `request.app.state` y no de un módulo global por dos razones: es
    por-request (dos apps pueden coexistir en el mismo proceso, que es justo lo que
    hace la suite de tests), y hace imposible que un test contamine a otro.
    """
    container: Container = request.app.state.container
    return container


def get_document_service(
    container: Annotated[Container, Depends(get_container)],
) -> DocumentService:
    """El servicio de documentos, listo para usar.

    Se declara `Annotated[..., Depends(...)]` en el punto de uso y no aquí como
    alias, para que la firma de cada endpoint muestre de un vistazo de dónde sale su
    dependencia.
    """
    return container.document_service
