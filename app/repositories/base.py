"""Contratos de la capa de datos.

`BaseRepository[T]` es la abstracción genérica que pide el enunciado (OT-9: es
generalización especulativa, con un solo subtipo, y se conserva por petición
explícita). `Page[T]` es el sobre de resultados paginados que comparten todas las
colecciones.
"""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

# `T` sin `bound`: sólo aparece en posiciones de salida (`Page[T]`, `list[T]` de
# retorno), así que un `bound` restrictivo sólo limitaría sin aportar seguridad
# real a un contrato de lectura. Además `TypeVar` en covariancia es exactamente lo
# que la anotación de retorno ya expresa.
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """Una ventana de resultados más los metadatos que el cliente necesita para
    paginar.

    `total` es el número de elementos de la colección **completa**, no el de la
    ventana. Es lo que evita que un cliente que pagina por `offset` se salte el
    final: si `total` fuera el tamaño de la página, vería la última página y
    concluiría que ya no hay nada.
    """

    items: list[T] = field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0


class BaseRepository(Generic[T]):
    """Contrato genérico de un repositorio.

    `Generic[T]` y no la sintaxis `class BaseRepository[T]`: la sintaxis nueva
    requiere Python 3.12 y el proyecto fija 3.11.

    **Todos los métodos son asíncronos**, y no por estilo: el driver (Motor, y
    también pymongo en su API asíncrona) no tiene forma síncrona. Declarar el
    contrato como síncrono obligaría a que la implementación real violase el
    contrato, y mypy lo detecta como error de override. El fake es asíncrono por
    el mismo motivo, para que interchangeably fulfnan el mismo contrato.

    Deliberadamente mínimo. Una interfaz con veinte métodos que una sola
    implementación usa cinco es una interfaz que nadie va a cumplir bien.
    """

    async def get_by_id(self, entity_id: str) -> T:
        """Devuelve la entidad o lanza `ResourceNotFoundException`."""
        raise NotImplementedError

    async def delete(self, entity_id: str) -> None:
        """Borra la entidad o lanza `ResourceNotFoundException`."""
        raise NotImplementedError

    async def list_paginated(self, limit: int, offset: int) -> Page[T]:
        """Devuelve una ventana de la colección, más nueva primero."""
        raise NotImplementedError
