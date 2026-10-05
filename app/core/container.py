"""Composition root: el único sitio donde se construyen las dependencias.

Fuera de aquí, nadie instancia un repositorio ni un servicio. Los routers piden
el servicio al contenedor mediante `Depends`, y no lo crean.

Es lo que hace que S3 se pueda probar entero sin base de datos: se pasa un
repositorio falso y el resto del grafo se monta igual que en producción.
"""

from dataclasses import dataclass

from app.repositories.pdf_repository import PdfRepository
from app.services.document_service import DocumentService


@dataclass(frozen=True, slots=True)
class Container:
    """Dependencias ya construidas, listas para inyectar.

    `frozen=True` y no una clase normal: un contenedor que se puede mutar en
    caliente acaba siendo estado global con otro nombre, que es justo el problema
    que evita.
    """

    document_service: DocumentService


def build_container(repository: PdfRepository) -> Container:
    """Construye el grafo completo a partir de un repositorio.

    El repositorio es la única dependencia que entra. Todo lo demás se crea aquí,
    y si mañana el servicio necesita el hash de documentos, esta función cambia en
    un sitio en vez de en cada router que construye el servicio a su manera.
    """
    return Container(document_service=DocumentService(repository))
