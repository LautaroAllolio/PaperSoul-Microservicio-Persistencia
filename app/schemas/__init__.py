"""DTOs de la frontera de transporte: lo que entra y sale por HTTP.

Viven fuera de `api/` a proposito: si estuvieran dentro, la capa de servicio
tendria que importar hacia arriba para tipar sus retornos, y la regla de
dependencias se romperia (SPEC.md §4).
"""
