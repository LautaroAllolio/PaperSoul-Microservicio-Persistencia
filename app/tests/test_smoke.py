"""Smoke test del toolchain (lote S0).

No prueba comportamiento de negocio: prueba que el arnes de tests está bien
configurado. Existe porque `asyncio_mode = "auto"` y
`asyncio_default_fixture_loop_scope` son opciones de pytest-asyncio 1.4, una API
que cambió respecto a la 0.x. Descubrirlo en el lote S3, con código de negocio ya
en juego, sería más caro que descubrirlo aquí.
"""

import pytest


async def test_asyncio_mode_auto_runs_async_tests_without_decorator() -> None:
    """Sin `@pytest.mark.asyncio`: si asyncio_mode no fuera "auto", no se ejecutaría.

    `asyncio_mode = "auto"` es lo que permite que `app/tests/` no repita el
    decorador en cada test async, y lo que hace que los fixtures async de
    `conftest.py` funcionen sin decoración explícita.
    """
    assert True


@pytest.mark.integration
def test_integration_marker_is_registered() -> None:
    """Ejercita el marcador `integration` de verdad.

    Con `--strict-markers`, usar un marcador no registrado rompe la recolección.
    Y la exclusión por `-m "not integration"` (gate SC-15) sólo funciona si el
    marcador existe. Este test es la prueba de que ambas cosas funcionan: la
    suite completa lo corre, la de integración lo deselecciona.
    """
    assert True


def test_app_package_is_importable() -> None:
    """La suite se importa como `app.tests`, no como `tests`.

    `pythonpath = ["."]` + `__init__.py` en el paquete hacen que los imports
    absolutos funcionen igual en local que en CI, sin depender del directorio
    desde el que se invoque pytest.
    """
    from app.tests import __name__ as module_name

    assert module_name == "app.tests"
