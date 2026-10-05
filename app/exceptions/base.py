"""Raíz de la jerarquía de errores de dominio.

Existe para que el handler HTTP necesite conocer **una sola** clase. Añadir un
error nuevo al servicio es añadir una clase en `domain.py` con dos atributos, sin
tocar ni el handler ni el router.

Deliberadamente no hereda de `HTTPException` de FastAPI: la capa de negocio no
conoce FastAPI. Importar el framework aquí invertiría la regla de dependencias
(SPEC.md §6).
"""

from app.core.problem_types import ProblemType


class PaperSoulError(Exception):
    """Raíz de todo error de dominio.

    Atributos de clase, no de instancia: `status_code`, `problem_type` y `title`
    describen el **tipo** de error, no la ocurrencia concreta. Son los mismos
    para todas las instancias de una clase dada.

    `detail`, en cambio, sí es de instancia: describe lo que pasó *en este caso*.
    """

    # 500 por defecto, no 400: si una subclase nueva olvida definir su status, el
    # fallo se manifiesta como error interno del servidor en vez de colarse en el
    # rango 4xx como si fuera culpa del cliente.
    status_code: int = 500
    problem_type: str = ProblemType.INTERNAL_ERROR
    title: str = "Error interno del servidor"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail
