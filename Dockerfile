# syntax=docker/dockerfile:1

# --- Etapa builder: resuelve dependencias con uv y crea el venv --------------
FROM python:3.11-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.3 /uv /usr/local/bin/uv

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Capa de dependencias: se cachea mientras pyproject.toml / uv.lock no cambien.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-install-project --no-dev

COPY app ./app
RUN uv sync --frozen --no-dev

# --- Etapa runtime: sólo el venv y el código ---------------------------------
FROM python:3.11-slim AS runtime

# Ejecutar como usuario no-root: el proceso no necesita escribir en el sistema.
RUN useradd --create-home --uid 1000 appuser

WORKDIR /app

COPY --from=builder --chown=appuser:appuser /app /app

ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1

USER appuser

EXPOSE 8000

# --host 0.0.0.0 es obligatorio: dentro del contenedor 127.0.0.1 no es accesible
# desde fuera. La seguridad la aporta la red, no el bind (SPEC.md §1.1 D-3).
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
