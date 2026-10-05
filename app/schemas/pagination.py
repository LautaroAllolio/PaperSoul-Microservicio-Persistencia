"""Sobre de resultados paginados (SPEC.md §4, `app/schemas/pagination.py`).

`Page[T]` es un concepto de **contrato**, no de persistencia: describe una ventana de
resultados más los metadatos que un cliente necesita para pedir la siguiente. Vive
aquí, y no en `repositories/base.py` donde estaba antes, por una razón que el test de
arquitectura hace cumplir de forma mecánica.

Cuando `Page` vivía en `repositories/base.py`, un router tenía que importarla de
allí para poder tipar la variable que le devolvía el servicio:

```python
# app/api/v1/documents.py — esto es lo que había
from app.repositories.base import Page
from app.models.pdf_document import StoredDocument

page: Page[StoredDocument] = await service.list_documents(...)
```

Es decir: **la capa de presentación dependía de la capa de datos** para nombrar un
tipo. La flecha del diagrama de `SPEC.md` §6 va al revés de lo que exige, y la
dependencia existía sólo por una anotación. Peor aún, `Page[StoredDocument]` arrastraba
el modelo de persistencia a la capa de presentación, que es la violación que §6
prohíbe de forma explícita como `api -> models`.

La razón de por qué `Page` no es un concepto de repositorio es que **no dice nada
sobre de dónde salen los datos**: el mismo sobre describe una ventana de Mongo, de un
`dict` en memoria o de una API externa. Lo que la capa de datos aporta es *llenarlo*,
no definir su forma. Por eso el sobre es genérico y no menciona documentos, y por
eso pertenece al vocabulario compartido y no a la capa que lo usa.
"""

from dataclasses import dataclass, field
from typing import Generic, TypeVar

# `T` sin `bound`: sólo aparece en posiciones de salida (`Page[T]`, y la lista de
# retorno), así que un `bound` restrictivo sólo limitaría sin aportar seguridad real a
# un contrato de lectura. Además, `TypeVar` en la anotación de retorno ya expresa
# exactamente lo mismo que la variancia.
T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Page(Generic[T]):
    """Una ventana de resultados más los metadatos que el cliente necesita para
    paginar.

    `total` es el número de elementos de la colección **completa**, no el de la
    ventana. Es lo que evita que un cliente que pagina por `offset` se salte el final:
    si `total` fuera el tamaño de la página, vería la última página y concluiría que
    ya no hay nada.
    """

    items: list[T] = field(default_factory=list)
    total: int = 0
    limit: int = 0
    offset: int = 0
