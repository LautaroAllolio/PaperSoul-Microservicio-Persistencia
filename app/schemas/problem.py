"""Modelo ProblemDetail de RFC 9457.

RFC 9457 ("Problem Details for HTTP APIs") define el cuerpo de toda respuesta
de error. Sus cinco miembros base son `type`, `title`, `status`, `detail` e
`instance`; ademas permite **miembros extension**: campos propios en la raiz del
objeto, junto a los base.
"""

from pydantic import BaseModel, ConfigDict

# Media type registrado en IANA para RFC 9457. Centralizado aqui para que ningún
# handler lo escriba a mano: repetir el literal en varios sitios es como se acaba
# sirviendo application/json por error en un endpoint.
PROBLEM_JSON = "application/problem+json"

# RFC 9457 §3.1: valor por defecto de `type` cuando no hay información sobre el
# problema. Significa "no sabemos", no "error genérico".
ABOUT_BLANK = "about:blank"


class InvalidParam(BaseModel):
    """Causa concreta de un rechazo por validación (miembro extension).

    `loc` conserva la ruta completa al campo, **índices de lista incluidos**:
    un error en el tercer elemento se reporta como `["body", "items", 2, "campo"]`
    y no aplanado. Sin el índice, el cliente sabe que un campo falló pero no cuál,
    y no puede corregir el payload.
    """

    loc: list[str | int]
    msg: str
    # Pydantic no siempre emite `type` en sus errores (p.ej. en uniones). Opcional
    # para no perder el resto de la información del error por eso.
    type: str | None = None


class ProblemDetail(BaseModel):
    """Cuerpo de error del servicio, conforme a RFC 9457.

    `extra="allow"` habilita los miembros extension de la RFC. Sin él, Pydantic
    los recortaría en silencio al construir el modelo, y un cliente perdería
    información que el servidor sí envía.
    """

    model_config = ConfigDict(extra="allow")

    type: str = ABOUT_BLANK
    # `title` es un resumen corto, estable y NO localizado: es la clave que un
    # cliente compara sin parsing frágil. `detail`, en cambio, sí se localiza y es
    # específico de cada ocurrencia.
    title: str
    status: int
    detail: str | None = None
    # URI de la ocurrencia concreta. Lo rellena el handler con request.url.path.
    instance: str | None = None
    invalid_params: list[InvalidParam] | None = None
    trace_id: str | None = None
