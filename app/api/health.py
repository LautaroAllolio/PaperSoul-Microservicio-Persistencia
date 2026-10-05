"""Sonda de salud.

Un endpoint de salud que no comprueba nada es peor que no tener ninguno: da una
respuesta verde a un servicio incapable de atender una petición real, y quien
vigila el despliegue deja de mirar por el otro extremo.

Por eso hay dos, con significados distintos:

- `/health`: el proceso está vivo. Lo usa el orquestador para decidir si reiniciar.
- `/ready`: además puede atender tráfico. Es lo que un balanceador debería mirar.

Aquí sólo se implementa `/health`. `/ready` llega con el contenedor de
dependencias en S4, porque necesita saber si hay conexión con la base de datos.
"""

from fastapi import APIRouter

router = APIRouter(tags=["health"])


@router.get("/health")
async def health() -> dict[str, str]:
    """Comprobación de vida del proceso.

    Deliberadamente no consulta Mongo: para eso está `/ready`. Si `/health`
    comprobara la base de datos, un Mongo lento provocaría reinicios del proceso,
    que es justo lo contrario de lo que se quiere.
    """
    return {"status": "ok"}
