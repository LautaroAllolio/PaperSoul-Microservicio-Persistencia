"""Arranque del logging.

Una sola responsabilidad: dejar el logging en el nivel pedido. El formato
incluye la hora y el nivel para que un log de error sea accionable sin
configurar un formateador externo.
"""

import logging

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s | %(message)s"


def configure_logging(level: str = "INFO") -> None:
    """Configura el logging raíz al nivel indicado.

    Args:
        level: Nombre de nivel de la biblioteca `logging` (p.ej. ``"DEBUG"``).

    Raises:
        ValueError: Si el nivel no existe. Un nivel mal escrito debe romper el
            arranque, no dejar el servicio usando el nivel por defecto en
            silencio, que es más difícil de diagnosticar que un fallo visible.
    """
    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if numeric_level is None:
        valid = ", ".join(name for name in logging.getLevelNamesMapping() if name.isupper())
        raise ValueError(f"Nivel de log inválido: {level!r}. Válidos: {valid}")

    logging.basicConfig(level=numeric_level, format=LOG_FORMAT, force=True)
