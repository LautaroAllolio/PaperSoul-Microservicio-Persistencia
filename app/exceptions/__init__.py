"""Excepciones de dominio.

Viven en la raiz de `app/` y no dentro de `api/` porque las lanzan los servicios
y las atrapan los handlers HTTP. Si estuvieran en `api/`, la capa de negocio
importaria hacia arriba (SPEC.md §4).
"""
