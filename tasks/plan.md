# Plan Técnico — PaperSoul Microservicio de Persistencia

> **Fase 2 (Plan) de SDD.** Derivado de [`../SPEC.md`](../SPEC.md), que es la fuente de verdad.
> Si este documento y la spec discrepan, **manda la spec**.

---

## 1. Componentes y dependencias

| # | Componente | Depende de | Responsabilidad |
|---|---|---|---|
| C1 | `core` | — | Config, logging, URNs, client Motor + `init_beanie`, composition root |
| C2 | `exceptions` | C1 (URNs) | Jerarquía `PaperSoulError` → errores de dominio |
| C3 | `schemas` | C1 | DTOs Pydantic + `ProblemDetail` + mappers |
| C4 | `models` | C1 | `PdfDocument` Beanie + índices |
| C5 | `repositories` | C1, C4 | Contrato `BaseRepository`/`PdfRepository` + `BeaniePdfRepository` |
| C6 | `services` | C2, C3, **C5 (ABC sólo)** | Casos de uso; **cero imports de beanie/motor** |
| C7 | `api` | C2, C3, C6, C1 | Routers, DI, exception handlers, OpenAPI |
| C8 | `main` | C1..C7 | `create_app()`, lifespan, metadata |
| C9 | `tests` | todos | Fakes, fixtures, suites, test de arquitectura |

**C6 depende del ABC, nunca de `BeaniePdfRepository`.** Es lo que hace que SC-13 sea verificable y lo
que acota el radio de impacto de una futura migración a PyMongo Async (SPEC §2.1).

**Dirección de dependencias:** sin ciclos. `core` no depende de nadie; `models` sólo de `core`;
`repositories` de `core`+`models`; `services` de `exceptions`+`schemas`+`repositories(ABC)`;
`api` de todo lo anterior. `main` es el único módulo que conoce todas las concreciones.

---

## 2. Orden de implementación

```
S0 ──► S1 ──► S2 ──► S3(POST) ──► S4(GET by id) ──► S5(GET list) ──► S6(GET by-hash) ──► S7(DELETE) ──► S8 ──► S9
```

**Vertical, no horizontal.** S2 entrega modelo + repositorio, y **cada endpoint posterior es un slice
completo y testeable por sí solo** — tras S3 el servicio ya persiste y devuelve 201 de verdad. Escribir
todos los repositorios y después todos los tests (o al revés) produce tests que verifican estructura
imaginada en vez de comportamiento. Cada slice es un *tracer bullet* que enseña algo al siguiente.

El detalle ejecutable de cada slice está en [`todo.md`](todo.md).

---

## 3. Diseño de modelos e interfaces

### 3.1 `PdfDocument` (Beanie, colección `pdf_documents`)

```python
class PdfDocument(Document):
    filename: str
    extracted_text: str
    extraction_method: ExtractionMethod
    page_count: int
    pdf_hash: str = Field(unique=True)                    # → índice único
    text_hash: str | None = None
    uploaded_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

    class Settings:
        name = "pdf_documents"
        indexes = [IndexModelField(keys=[("uploaded_at", DESCENDING), ("_id", DESCENDING)],
                                   name="uploaded_at_desc")]
```

El índice compuesto existe **porque SC-05**. Sin él, Mongo ordenaría en memoria y saltaría el límite de
32 MB al listar.

### 3.2 Contrato del repositorio (3 niveles)

```
BaseRepository[TDocument, TId]        # ABC genérico: create, get_by_id, delete, count
  └── PdfRepository                    # ABC: + get_by_hash, exists_by_hash, list_paginated
        └── BeaniePdfRepository        # única clase que importa Beanie — el adapter
```

> Se reconocen 3 clases donde la lectura estricta de SOLID pediría 1: `BaseRepository` tiene un solo
> subtipo, lo que es generalización especulativa. **Se conserva porque el enunciado lo pide
> explícitamente** (OT-9), no porque el diseño lo exija. Sirve como punto de extensión cuando `Validar`
> necesite persistir su veredicto.

`BeaniePdfRepository` traduce `DuplicateKeyError` de PyMongo a `DuplicateResourceException` **en la capa
de datos**: es ella quien sabe que el índice es único, no el servicio.

`list_paginated(limit, offset)` aplica `sort([("uploaded_at", -1), ("_id", -1)])` + `skip`/`limit` y
devuelve `(items, total)`.

### 3.3 `PdfDocumentService`

Cinco casos de uso: `create`, `get`, `list`, `exists_by_hash`, `delete`.

Responsabilidades: default de `uploaded_at` en UTC, traducción de `None` → `ResourceNotFoundException`,
mapeo modelo → DTO. **Los routers no deciden nada.**

---

## 4. Mapeo centralizado de errores (RFC 9457)

`PaperSoulError` lleva `status_code`, `problem_type`, `title` como **atributos de clase**, no de
instancia: son datos del *tipo* de error, no de la ocurrencia. `detail` sí es de instancia.

`api/errors.py` registra **tres** handlers:

| Handler | Para | Produce |
|---|---|---|
| `paper_soul_error_handler` | `PaperSoulError` (toda la jerarquía) | status + `type`/`title` de la clase |
| `request_validation_error_handler` | `RequestValidationError` | 422 + `invalid_params` con `loc` fiel |
| `unhandled_exception_handler` | `Exception` | 500 + `trace_id`, sin filtrar internals al cliente |

`PROBLEM_JSON = "application/problem+json"`. Un cuarto handler para `Content-Type` (415) vive en el
mismo módulo, en el lote 9.3.

**Por qué un handler genérico:** añadir un error nuevo es añadir *una* clase con dos atributos. Ni el
handler ni el router cambian. Un `except` por excepción acoplaría el transporte a cada caso de negocio.

---

## 5. Verificación entre fases (gates)

| Gate | Momento | Comando | Criterio |
|---|---|---|---|
| **G0** | Fin S0 | `uv sync && uv run python -V && uv run ruff check .` | Entorno resuelve, Python 3.11, lint limpio |
| **G1** | Fin S3.4 | `pytest -k test_pdf_hash_index_is_unique` | `index_information()` reporta `pdf_hash` único |
| **G2** | Cada slice S4–S7 | `uv run pytest -m "not integration"` + el archivo de API del slice | Verde sin Docker |
| **G3** | Fin S8 | `uv run pytest` | SC-09..SC-12 verdes |
| **G4** | Fin S9 | `uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest` | SC-13..SC-20 verdes |

> **G1, G3 y la mitad de G2 requieren Docker**, que no está instalado en la máquina de desarrollo
> (SPEC §2.2, OT-11). Hasta que se instale, esos gates sólo son verificables en CI.

---

## 6. Riesgos y mitigación

| Riesgo | Impacto | Mitigación |
|---|---|---|
| **Beanie 2.x entra como transitive** y rompe el import de Motor | Arranque roto | Pin `==1.30.0`; `uv.lock` commiteado; SC-14 |
| **Motor en EOL** — sin features, sólo fixes críticos hasta 2027-05-14 | Deuda técnica | Ruta de escape aislada en `core/database.py`; el patrón Repository acota el radio de impacto a 2 ficheros |
| **`Task attached to a different loop`** | Suite intermitente | Contenedor session-scope expone **sólo la URI**; `init_beanie` por función; SC-15 |
| **`CollectionWasNotInitialized`** | Fallos confusos en tests | Fixture `beanie_client` autouse |
| **Fuga de event loop / warnings de asyncio** | Ruido que oculta bugs reales | `asyncio_default_fixture_loop_scope="function"` |
| **`uploaded_at` pierde el offset** al releer de Mongo | Contrato roto en silencio | `tz_aware=True` + SC-07 explícito |
| **Paginación inconsistente** con `uploaded_at` repetido | Ítems duplicados o perdidos | Índice `(uploaded_at, _id)` + SC-05 |
| **`extracted_text` > 16 MB** | 500 opaco del driver | Techo 10M chars → 422 + SC-12 |
| **Tests con falso verde** si el fake no implementa unicidad | US-2 sin verificar | Fake con unicidad real + test que comprueba `index_information()` |
| **415 rompe clientes del orquestador** | 4xx inesperado en `Extraer` | Decisión documentada SPEC §1.1 D-2; `additionalProperties:false` + SC-21 dan el error claro |
| **Sin autenticación** (§1.1 D-3) | Servicio expuesto sin control de acceso | Puertos de compose en loopback; advertencia en README. No exponer fuera de la red interna |
| **Docker ausente en la máquina de desarrollo** | G1/G2/G3 no verificables localmente | `pytest -m "not integration"` cubre el loop TDD; integración se valida en CI |

---

## 7. Paralelizable vs. secuencial

**Secuencial:** S0→S1→S2 (andamiaje, cada pieza depende de la anterior), y S8→S9 (los handlers de error
tucan todo el árbol de endpoints).

**Paralelizable:** los slices S3–S7 comparten `DocumentService`, `ProblemDetail` y los fakes, pero una
vez S2 cierra, cada uno toca un grupo de archivos distinto. Con varias personas, S3–S7 pueden avanzar en
paralelo sobre ramas `feat/sN-*`.

**Con una sola persona: secuencial.** Es el orden planificado.
