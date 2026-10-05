"""Tests de Settings y del arranque de logging (lote S1, tarea 1.1)."""

import logging

import pytest

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging


def test_settings_defaults_are_the_agreed_ones() -> None:
    """Los defaults deben coincidir con .env.example.

    Si divergen, el despliegue silencioso apunta el servicio a una base de datos
    equivocada sin que nada falle.
    """
    settings = Settings(_env_file=None)

    assert settings.mongodb_uri == "mongodb://localhost:27017"
    assert settings.mongodb_database == "papersoul"
    assert settings.log_level == "INFO"
    assert settings.problem_type_base == "urn:problem:papersoul"


def test_settings_read_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """La configuración viene del entorno, no del código (SPEC.md §7)."""
    monkeypatch.setenv("MONGODB_DATABASE", "papersoul_test_env")

    assert Settings(_env_file=None).mongodb_database == "papersoul_test_env"


def test_get_settings_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    """`get_settings()` se cachea para no releer el entorno en cada inyección.

    Sin caché, cada resolución de dependencia releería el entorno y el `.env`.
    El test comprueba la identidad del objeto, no sólo los valores: es la caché
    lo que se está verificando.
    """
    get_settings.cache_clear()
    monkeypatch.setenv("MONGODB_DATABASE", "primera")

    first = get_settings()
    monkeypatch.setenv("MONGODB_DATABASE", "segunda")

    assert get_settings() is first
    assert first.mongodb_database == "primera"


def test_configure_logging_applies_the_requested_level() -> None:
    """El nivel de log es configurable sin tocar código."""
    configure_logging("DEBUG")

    assert logging.getLogger().level == logging.DEBUG


def test_configure_logging_rejects_unknown_level() -> None:
    """Un nivel inválido debe fallar al arrancar.

    Si se aceptara en silencio, el servicio quedaría con el nivel por defecto y
    nadie vería logs, que es peor que un arranque fallido y explícito.
    """
    with pytest.raises(ValueError):
        configure_logging("VERBOSO_INVENTADO")
