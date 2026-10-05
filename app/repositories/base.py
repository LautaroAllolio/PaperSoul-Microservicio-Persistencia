"""Contratos de la capa de datos.

`BaseRepository[T]` es la abstracción genérica que pide el enunciado (OT-9: es
generalización especulativa, con un solo subtipo, y se conserva por petición
explícita).

`Page[T]` **no** se define aquí sino que se importa de `app/schemas/pagination.py`, y
es deliberado. `Page` describe una ventana de resultados, no una consulta: no menciona
la base de datos, y por eso pertenece al vocabulario compartido. Definirla en esta
capa obligaba a los routers a importarla desde aquí para poder tipar el retorno del
servicio, es decir, invertía la regla de dependencias de `SPEC.md` §6 por el motivo
más inocuo del mundo: una anotación de tipo. El módulo `pagination.py` explica el
caso con detalle.
"""

from typing import Generic, TypeVar

from app.schemas.pagination import Page

# `T` sin `bound`: sólo aparece en posiciones de salida (`Page[T]`, `list[T]` de
# retorno), así que un `bound` restrictivo sólo limitaría sin aportar seguridad
# real a un contrato de lectura. Además `TypeVar` en covariancia es exactamente lo
# que la anotación de retorno ya expresa.
T = TypeVar("T")


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
