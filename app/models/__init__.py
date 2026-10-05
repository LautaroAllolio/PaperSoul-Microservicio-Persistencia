"""Modelos Beanie: el contrato hacia MongoDB.

Viven aparte de `repositories/` por simetría con `schemas/`: `schemas/` es el
contrato hacia el transporte y `models/` el contrato hacia la base de datos.
Agruparlos obligaría a una capa a importar de la otra (SPEC.md §4).
"""
