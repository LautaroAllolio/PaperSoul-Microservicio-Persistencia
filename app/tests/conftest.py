"""Fixtures compartidas por toda la suite.

Viven en la raíz de `tests/` porque afectan a **todos** los niveles (unit, api,
integration) y no a uno concreto.
"""

from collections.abc import Iterator

import pytest

from app.core.config import get_settings


@pytest.fixture(autouse=True)
def reset_settings_cache() -> Iterator[None]:
    """Limpia el caché de `get_settings` antes y después de cada test.

    Existe porque `get_settings` está cacheada con `lru_cache` a propósito, y
    `monkeypatch.setenv` **no** deshace esa caché: el entorno vuelve a su estado
    anterior, pero el objeto `Settings` ya construido se queda con el valor
    anterior contaminando todo lo que venga después.

    El síntoma es desconcertante: un test que pasa en aislamiento y falla dentro de
    la suite con un valor que ningún test parece haber puesto. Fue lo que pasó con
    `MONGODB_DATABASE=primera`, que se colaba desde el test del caché hasta el test
    del lifespan, que esperaba `papersoul` y leía `primera`.

    Se limpia en los dos extremos para que ningún test pueda ver la configuración de
    otro, sea cual sea el orden de ejecución.
    """
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()
