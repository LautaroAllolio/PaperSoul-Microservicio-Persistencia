"""Agregador de la versión 1 de la API.

Existe para que el prefijo `/api/v1` esté declarado **en un solo sitio**. Añadir una
versión nueva es añadir un módulo más aquí, no editar cinco `include_router` con
prefiijos distintos que se van desincronizando.
"""

from fastapi import APIRouter

from app.api.v1.documents import router as documents_router

api_router = APIRouter()
api_router.include_router(documents_router)
