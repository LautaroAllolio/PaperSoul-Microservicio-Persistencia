"""Configuración del servicio, leída del entorno.

Una sola responsabilidad: exponer los ajustes de ejecución. No abre conexiones ni
llega a registrar nada; eso es trabajo de `database.py` y `logging.py`
respectivamente (SRP).
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Ajustes de ejecución del microservicio.

    Todos los campos tienen valor por defecto para que el servicio arranque sin
    configuración explícita en desarrollo. En producción los sobreescribe el
    entorno (SPEC.md §7: Ask first → tocar configuración).
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    mongodb_uri: str = "mongodb://localhost:27017"
    mongodb_database: str = "papersoul"
    log_level: str = "INFO"
    problem_type_base: str = "urn:problem:papersoul"

    # Metadata de OpenAPI. Vive en la configuración y no como literal en `main.py`
    # porque el `title` lo consume la cátedra y el orquestador al generar cliente;
    # tener que editar `main.py` para renombrar el servicio sería un acoplamiento
    # innecesario entre la identidad del servicio y el punto de arranque.
    app_title: str = "PaperSoul - Microservicio de Persistencia"
    app_version: str = "0.1.0"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Devuelve la configuración cacheada.

    El caché evita releer el entorno y el `.env` en cada resolución de
    dependencias, que ocurre una vez por request.
    """
    return Settings()
