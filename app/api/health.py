"""Sondas de salud y disponibilidad del servicio."""

import asyncio
import logging

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.api.errors import new_trace_id, problem_response
from app.core.problem_types import ProblemType
from app.schemas.problem import ProblemDetail

router = APIRouter(tags=["health"])
logger = logging.getLogger(__name__)


@router.get("/health")
async def health() -> dict[str, str]:
    """Comprobación de vida del proceso.

    Deliberadamente no consulta Mongo: para eso está `/ready`. Si `/health`
    comprobara la base de datos, un Mongo lento provocaría reinicios del proceso,
    que es justo lo contrario de lo que se quiere.
    """
    return {"status": "ok"}


@router.get("/ready")
async def ready(request: Request) -> JSONResponse:
    """Comprueba si Mongo puede atender una operación en un máximo de 500 ms."""
    try:
        mongo_client = request.app.state.mongo_client
        if mongo_client is None:
            raise ConnectionError("MongoDB client is not initialized")
        await asyncio.wait_for(mongo_client.admin.command("ping"), timeout=0.5)
    except Exception:
        trace_id = new_trace_id()
        logger.exception("MongoDB no está disponible [trace_id=%s]", trace_id)
        return problem_response(
            ProblemDetail(
                type=ProblemType.DATABASE_UNAVAILABLE,
                title="Base de datos no disponible",
                status=503,
                detail="La base de datos no está disponible.",
                instance=request.url.path,
                trace_id=trace_id,
            )
        )

    return JSONResponse(content={"status": "ready", "database": "connected"})
