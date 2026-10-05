# Checklist de Ejecución — PaperSoul Microservicio de Persistencia

> **Fase 3 (Tasks) de SDD.** Derivado de [`plan.md`](plan.md) y [`../SPEC.md`](../SPEC.md).
> Ejecución: un lote = una sesión. **Un commit = una tarea.**

---

## Estrategia de Git

- **Rama por lote:** `feat/s3-post-document`, `test/s9-ci`, etc. `main` sólo recibe merges con CI verde.
- **Conventional Commits estricto:** `tipo(ámbito): descripción en imperativo`.
- **Ámbitos estables:** `env` · `core` · `errors` · `model` · `repo` · `service` · `api` · `test` · `ci` · `docs`
- **Nunca** mezclar refactor con feature en el mismo commit.
- **`SPEC.md` viaja en el primer commit de arquitectura.** Es fuente de verdad, no documentación posterior.

**Gate universal antes de cada push:**

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app && uv run pytest
```

| Legenda | Significado |
|---|---|
| 🟢 | Verificable **sin Docker** en esta máquina |
| 🔵 | Requiere Docker (Testcontainers) — sólo CI hasta que se instale Docker Desktop |

---

## Lote S0 — Bootstrap del entorno

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 0.1 | Proyecto uv + pin exacto de Beanie + tooling | `pyproject.toml`, `.python-version`, `uv.lock`, `app/__init__.py` | `chore(env): bootstrap uv project with pinned beanie 1.30 and motor` | `uv sync` resuelve; `uv run python -V` = 3.11 🟢 |
| 0.2 | Spec y plan en control de versiones | `SPEC.md`, `tasks/plan.md`, `tasks/todo.md` | `docs(spec): add SDD spec, technical plan and task breakdown` | Revisión humana |
| 0.3 | Entorno local de Mongo | `docker-compose.yml`, `Dockerfile`, `.env.example`, `.dockerignore` | `chore(env): add docker compose for mongodb and app image` | `docker compose up -d mongo` 🔵 |
| 0.4 | `.gitignore` extendido | `.gitignore` | `chore(env): ignore venv, caches and local env files` | `git check-ignore` 🟢 |
| 0.5 | Smoke test del toolchain | `app/tests/__init__.py`, `app/tests/test_smoke.py` | `test(env): add toolchain smoke test for pytest-asyncio auto mode` | `pytest -m "not integration"` 🟢 |

> **Nota sobre 0.1:** la definición de dependencias y la configuración de ruff/mypy/pytest viven en el
> mismo `pyproject.toml`. Separarlas exigiría `git add -p` interactivo por hunks, así que son **un
> commit**: son ambas "definición del proyecto". El commit 0.5 (smoke test) **no estaba en el plan
> original**; se añadió al comprobar que `pytest` salía con código 5 (*no tests collected*) porque
> `testpaths` apuntaba a un directorio inexistente. Sin él, el gate G0 no cierra en verde.


### Detalle

**0.1** — `requires-python = ">=3.11,<3.14"`. `dependencies` con **`beanie==1.30.0`** (pin exacto, **sin
caret**), `motor>=3.6,<4.0`, `fastapi>=0.142`, `uvicorn[standard]`, `pydantic>=2.9`,
`pydantic-settings>=2.6`. `[dependency-groups] dev` con `pytest`, `pytest-asyncio`, `pytest-cov`,
`httpx`, `asgi-lifespan`, `testcontainers[mongo]`, `ruff`, `mypy`. `[build-system]` hatchling.
**Criterio:** `uv lock` no resuelve Beanie 2.x; `uv.lock` commiteado (SC-18).

**0.2** — `[tool.pytest.ini_options]`: `asyncio_mode = "auto"`,
`asyncio_default_fixture_loop_scope = "function"`, `testpaths = ["app/tests"]`,
`markers = ["integration: requiere MongoDB via Testcontainers"]`, `addopts = "--strict-markers"`.
`[tool.ruff]`: `target-version = "py311"`, `line-length = 100`, `select` con `E,F,I,N,UP,B,SIM,RUF,ASYNC,ARG,PT,PTH`; `per-file-ignores` para tests. `[tool.mypy]`: `plugins = ["pydantic.mypy"]`, `ignore_missing_imports` para `beanie.*`/`motor.*`, `disallow_untyped_defs = true`.
**Criterio:** los tres comandos salen con código 0 (SC-14) 🟢

**0.3** — `docker-compose.yml`: servicio `mongo` (imagen `mongo:7`, puerto en **loopback** `127.0.0.1:27017`
— ver SPEC §1.1 D-3, sin auth) con `healthcheck` de `mongosh ping`, y `mongo-express` (UI de exploración,
también loopback) dependiente del estado *healthy* de `mongo`. `Dockerfile` multi-stage: builder con
`uv` (`--no-dev`), imagen final con sólo el venv, usuario **no-root** (`appuser`, uid 1000).
`.env.example` con `MONGODB_URI`, `MONGODB_DATABASE`, `LOG_LEVEL`, `PROBLEM_TYPE_BASE` — **sin secretos
reales**. `.dockerignore` para que la imagen no arrastre `.git`, tests ni `.env`.

**0.4** — Además de la sección de PaperSoul, se **cierra un hueco de secretos**: el `.gitignore`
base sólo ignoraba `.env` exacto, de modo que un `.env.local` o `.env.production` con credenciales de
Mongo habría acabado en el repositorio. Se añade `.env.*` con la excepción `!.env.example`.
**Criterio:** `git check-ignore` confirma que `.env`, `.env.local` y `.env.production` se ignoran y que
`.env.example` y `uv.lock` **no**. 🟢


---

## Lote S1 — Cimientos (`core`)

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 1.1 | Config por entorno | `app/core/config.py`, `app/core/logging.py` | `feat(core): add pydantic-settings config and structured logging` | test de defaults 🟢 |
| 1.2 | URNs de `type` | `app/core/problem_types.py` | `feat(errors): define RFC 9457 problem type URNs` | test de unicidad 🟢 |

**1.1** — `Settings(BaseSettings)` con `mongodb_uri`, `mongodb_database`, `log_level`,
`problem_type_base`. Test: los defaults son seguros y `get_settings()` cachea. 🟢

**1.2** — `ProblemType(StrEnum)` con los 7 slugs de SPEC §5.3. Test: los 7 valores son únicos y todos
empiezan por `urn:problem:papersoul:`. Si se añade uno, el test obliga a documentarlo en la spec. 🟢

---

## Lote S2 — Vertical de errores (RFC 9457)

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 2.1 | `ProblemDetail` | `app/schemas/problem.py` | `feat(errors): add RFC 9457 ProblemDetail model with invalid_params` | SC-10 🟢 |
| 2.2 | Jerarquía de dominio | `app/exceptions/base.py`, `app/exceptions/domain.py` | `feat(errors): add domain exception hierarchy` | 100% cobertura 🟢 |
| 2.3 | Handlers globales | `app/api/errors.py` | `feat(errors): add global handlers returning application/problem+json` | SC-09 🟢 |

**2.1** — `ProblemDetail` con `model_config = ConfigDict(extra="allow")` (miembros extension de RFC 9457),
`InvalidParam` con `loc: list[str | int]`. **Criterio clave:** `loc` conserva el índice de lista
(`["body","items",2,"field"]`, no aplanado) — SC-10. 🟢

**2.2** — `PaperSoulError` con `status_code`/`problem_type`/`title` como **atributos de clase**;
`ResourceNotFoundException`, `DuplicateResourceException`, `InvalidDocumentIdException`.
**Criterio:** 100% cobertura en `app/exceptions/` (SC-17). 🟢

**2.3** — Tres handlers (§4 del plan). El `trace_id` se genera con `uuid4()` y se registra en el log con
el `detail` — nunca el `extracted_text` (SPEC §7). **Criterio:** `Content-Type: application/problem+json`
y cuerpo con `type`,`title`,`status`,`instance` — SC-09. 🟢

---

## Lote S3 — Modelo, repositorio y fakes

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 3.1 | Modelo Beanie | `app/models/pdf_document.py` | `feat(model): add PdfDocument with unique pdf_hash index` | test de `Settings` e índices 🔵 |
| 3.2 | Fixtures y fakes | `app/tests/conftest.py`, `app/tests/fakes.py` | `test(env): add testcontainers fixtures and in-memory fakes` | `pytest -m "not integration"` verde 🟢 |
| 3.3 | Contrato del repositorio | `app/repositories/base.py` | `refactor(repo): add BaseRepository contract` | `mypy` = 0 🟢 |
| 3.4 | `PdfRepository` + impl | `app/repositories/pdf_repository.py` | `feat(repo): implement PdfRepository with beanie` | **G1** SC-03 🔵 |
| 3.5 | DTOs y mappers | `app/schemas/document.py`, `pagination.py`, `mappers.py` | `feat(schema): add document DTOs and beanie-to-schema mappers` | test de mappers 🟢 |

**3.1** — `PdfDocument` con `pdf_hash: str = Field(unique=True)` y el índice
`[("uploaded_at", DESC), ("_id", DESC)]`. `extraction_method: ExtractionMethod`. 🟢 para el test de
declaración; 🔵 para comprobar que Mongo crea los índices.

**3.2** — ⚠️ **La tarea que más tiempo va a costar.** Tres trampas resueltas aquí, no descubiertas en CI
(SPEC §8.3):
1. Fixture `mongo_container` **session-scope que sólo devuelve la URI string** — nunca un driver, que es
   la causa del `Task attached to a different loop`.
2. `beanie_client` **autouse**, por función, llamando `init_beanie` con `PdfDocument` — sin él,
   `CollectionWasNotInitialized`.
3. `AsyncClient` + `ASGITransport` envuelto en `LifespanManager` — `ASGITransport` **no** dispara lifespan.

`FakePdfRepository` en `fakes.py`: dict en memoria **con la unicidad de `pdf_hash` implementada de
verdad** (lanza `DuplicateResourceException`), no un `Mock` que devuelve lo que el test espera.
Base de datos con nombre único por test: `papersoul_test_{uuid4().hex[:8]}`.

**3.4** — `BeaniePdfRepository`. Traduce `DuplicateKeyError` → `DuplicateResourceException` **en la capa
de datos** (es quien sabe que el índice es único). `list_paginated` con `sort([("uploaded_at",-1),("_id",-1)])`
+ `skip`/`limit`, devuelve `(items, total)`.
**Criterio — GATE G1:** `PdfDocument.get_motor_collection().index_information()` reporta el índice
`uniq_pdf_hash` con `unique: true` contra Mongo real (SC-03). 🔵

> **Estado de G1: tests escritos, SIN EJECUTAR.** `6f04d72` añade 16 tests en
> `app/tests/integration/` más las fixtures de `conftest.py`. No se han podido correr porque esta máquina
> no tiene Docker Desktop; sólo se ha comprobado que **colectan** (`pytest --collect-only`) y que
> `pytest -m "not integration"` sigue verde. El gate **no está cerrado**: se cierra en CI (SC-20) o en
> una máquina con Docker. No marcar G1 como verde sin ejecutarlo.
>
> Los índices se consultan por su **nombre explícito** (`uniq_pdf_hash`, `idx_uploaded_at_id_desc`), no
> por los nombres que Mongo genera (`pdf_hash_1`). Es intencionado: consultar el nombre declarado
> detecta que Beanie no lo creó, que es el fallo que G1 existe para encontrar.
>
> Lo primero que puede fallar al ejecutarlos: `init_beanie` se llama una vez por test (base de datos
> distinta cada vez) y **es estado global**. Si Beanie 1.30 se quejara de re-inicializar, la solución es
> un `init_beanie` por sesión más un `swap_database` por test, no relajar el aislamiento.

**3.5** — `DocumentCreateRequest` con `pattern: ^[0-9a-f]{64}$` en `pdf_hash` y `text_hash` (SPEC D-1, sin
flag `i`), `additionalProperties: false`, `maxLength: 10000000` en `extracted_text`.
`DocumentResponse`, `DocumentPersistedResponse`, `DocumentListResponse`, `HashCheckResponse`.
`mappers.py` con `to_response(doc) -> DocumentResponse` y `to_list_response(items, total, limit, offset)`.
🟢

---

## Lote S4 — Slice POST

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 4.1 | Caso de uso `create` | `app/services/document_service.py`, `tests/unit/test_document_service.py` | `feat(service): implement create_document with duplicate detection` | test de traducción de duplicado 🟢 |
| 4.2 | Ruta + DI | `app/api/v1/documents.py`, `app/api/dependencies.py`, `tests/api/test_documents_api.py` | `feat(api): add POST /api/v1/documents returning 201` | SC-01 🔵 |

**4.1** — `create` asigna `datetime.now(UTC)` si `uploaded_at` es `None`. Test con `FakePdfRepository`:
crea correctamente, y traduce el duplicado a `DuplicateResourceException`. 🟢

**4.2** — `POST /api/v1/documents` → 201 + `Location: /api/v1/documents/{id}` + `{"id","status":"persisted"}`.
`documents.py` declara **antes** el router `by-hash` que el `{doc_id}` (SPEC §5.2). SC-01. 🔵

---

## Lote S5 — Slice GET by id

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 5.1 | `get` + validación de ObjectId | `app/services/document_service.py` | `feat(service): add get_document with object id validation` | test 400 vs 404 🟢 |
| 5.2 | Ruta `GET /{doc_id}` | `app/api/v1/documents.py` | `feat(api): add GET /documents/{doc_id} with 400 and 404 handling` | SC-06 🔵 |

**5.1** — `ObjectId.is_valid(doc_id)`: si no → `InvalidDocumentIdException` (**400**), si sí pero no existe
→ `ResourceNotFoundException` (**404**). El corte 400/404 es semántico: 400 = formato imposible,
404 = bien formado pero ausente. 🟢

---

## Lote S6 — Slice GET listado

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 6.1 | `list` con paginación | `app/services/document_service.py` | `feat(service): add list_documents with stable pagination` | test de orden 🟢 |
| 6.2 | Ruta `GET /documents` | `app/api/v1/documents.py` | `feat(api): add paginated GET /documents ordered by uploaded_at desc` | SC-05 🔵 |

**6.2** — Query `limit` (1..100, def. 20), `offset` (≥0, def. 0). Orden `uploaded_at DESC, _id DESC`.
**Criterio:** con `uploaded_at` repetido, paginar no repite ni pierde ítems entre páginas — SC-05. 🔵

---

## Lote S7 — Slice GET by-hash

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 7.1 | `exists_by_hash` | `app/services/document_service.py` | `feat(service): add exists_by_hash returning existence envelope` | test de ambos casos 🟢 |
| 7.2 | Ruta `GET /by-hash/{pdf_hash}` | `app/api/v1/documents.py` | `feat(api): add GET /documents/by-hash returning exists envelope` | SC-04 🔵 |

**7.2** — **Siempre 200.** No existe → `{"exists": false, "id": null, "uploaded_at": null}`. Un 404
obligaría al orquestador a capturar excepciones en el camino caliente (SPEC §10 OT-2). SC-04. 🔵

---

## Lote S8 — Slice DELETE

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 8.1 | `delete` | `app/services/document_service.py` | `feat(service): add delete_document raising not found when absent` | test 404 🟢 |
| 8.2 | Ruta `DELETE /{doc_id}` | `app/api/v1/documents.py` | `feat(api): add DELETE /documents/{doc_id} returning 204` | SC-08 🔵 |

**8.2** — 204 **sin cuerpo**. Segunda llamada → 404. SC-08. 🔵

---

## Lote S9 — Cerrar el servicio

| # | Tarea | Archivos | Commit | Verificación |
|---|---|---|---|---|
| 9.1 | `create_app` + lifespan | `app/main.py` | `f002de1 feat(core): add injectable startup with DI container and health endpoint` | SC-19 🟢 |
| 9.2 | Health | `app/api/health.py` | `f002de1 feat(core): add injectable startup with DI container and health endpoint` | test 🟢 |
| 9.3 | Contrato de errores en API | `app/tests/api/test_errors_rfc9457.py` | `test(api): add RFC 9457 error contract tests` | SC-09..SC-12, SC-19, SC-21, SC-22 🟢 |
| 9.4 | Handler de 415 | `app/api/dependencies.py` + `app/api/errors.py` | `feat(api): return 415 for non-json content type and 400 for malformed json` | SC-21 🟢 |
| 9.5 | Test de arquitectura | `app/tests/test_architecture.py` | `test(arch): enforce layer dependency rule by AST` | SC-13 🔴 **NO HECHO** |
| 9.6 | Umbral de cobertura | `pyproject.toml` | `chore(test): enforce coverage thresholds` | SC-17 🟢 |
| 9.7 | CI | `.github/workflows/ci.yml` | `ci: run lint, typecheck, unit and integration jobs` | SC-20 🔵 (sin ejecutar) |
| 9.8 | README | `README.md` | `docs(readme): document setup, endpoints and runbook` | Revisión humana 🔵 |

**9.1** — `create_app()` con `FastAPI(title, version, lifespan)`, metadata OpenAPI 3.1, `docs_url` en dev.
El `lifespan` crea el cliente Motor con **`tz_aware=True`** (SC-07), llama `init_beanie` y **cierra el
cliente con `await`** al salir. 🟢 Hecho en `f002de1` (adelantado a S3 para poder testear S4 sin Docker).

Decisiones que se tomaron al implementarlo, y que el enunciado no fijaba:

- `create_app` recibe `repository`, `client_factory` e `init_beanie_func`, todos con default de
  producción. La app se puede construir entera con fakes, que es la condición de **9.3** y de todo S4.
- El cliente se pide **fuera** del `try` del lifespan. Si se creara dentro y su construcción fallara,
  el `finally` vería `None` y no podría cerrar nada. Cubierto por
  `test_lifespan_closes_the_database_even_when_startup_fails`.
- `init_database` ya **no** crea el cliente: sólo inicializa Beanie sobre uno existente. Separar
  "conectar" de "preparar el ODM" es lo que permite que el test del fallo sea posible.
- El `Container` se publica en `app.state` para que `Depends` lo resuelva en S4.

**Codificación (nota de proceso).** Los ficheros de 3.4 y 3.5 se escribieron con
`Set-Content -Encoding UTF8` en PowerShell 5.1, que interpreta UTF-8 como Windows-1252 y produce
texto doblemente codificado. No rompía los tests (el código ejecutable es ASCII) pero hacía fallar
`RUF002`. Reparado en `4fcb65e`. **Regla: editar ficheros de texto con las herramientas de
edición, nunca con cmdlets de PowerShell.**

**9.3** — Matriz de contrato: cada status × `Content-Type: application/problem+json` × presencia de
`type`/`title`/`status`/`instance`. Incluye `invalid_params` con índice de lista, campo desconocido
(`pdfHash` → 422), `extracted_text` de 10 000 001 chars (→ 422), `pdf_hash` en mayúsculas (→ 422, D-1),
`Content-Type: text/plain` (→ 415). 28 tests. Añadida también la comprobación de SC-19: todo 4xx/5xx del
OpenAPI se declara `application/problem+json` con `$ref` a `ProblemDetail`.

**9.4** — El 415 se valida **antes** de la validación de Pydantic: si el body llega como `text/plain`,
FastAPI lo reporta como error de JSON y la respuesta sería 422, no 415.

Dónde vive el 415 y por qué no en el handler: `require_json_content_type` es una **dependencia** de
`POST /documents`, no un `if` dentro del endpoint. FastAPI lee y parsea el body *antes* de invocar la
función del endpoint, así que un `raise` en su cuerpo llega tarde para un body que ni siquiera es JSON;
la dependencia se resuelve antes. La comparación es por **media type** (`application/json;
charset=utf-8` y `application/*+json` se aceptan), nunca por cabecera entera. Sin `Content-Type` solo
se acepta si no hay body: se lee `await request.body()` y no `Content-Length`, porque `"0"` es un valor
truthy y las peticiones `chunked` no lo declaran.

**Malformed JSON (400)** se resuelve en `request_validation_error_handler`: FastAPI convierte
`json.JSONDecodeError` en un `RequestValidationError` con `type: "json_invalid"`, así que el 400 sale de
bifurcar por ese `type` en vez de añadir un handler nuevo. Los atributos (`type`, `title`, `status`) se
leen de una instancia de `MalformedJsonException` para no duplicar los literales del contrato en un
segundo sitio.

**SC-19 (media type en el OpenAPI)** — FastAPI documenta el `model` de una respuesta adicional siempre
bajo el media type de la clase de respuesta, que es `application/json`, y no hay forma de declararlo
como `problem+json` desde el decorador. Por eso `app/api/openapi_problem.py` hace dos cosas:
`problem_responses()` declara el modelo (que es lo que registra `ProblemDetail` en `components.schemas`)
y `use_problem_media_types()` mueve después el media type de las respuestas cuyo `$ref` sea
`ProblemDetail` o `HTTPValidationError`. Se identifica por `$ref` y no por una lista de endpoints, para
que uno nuevo quede bien sin que nadie lo recuerde. El `HTTPValidationError` autogenerado por FastAPI
para el 422 también se reescribe a `ProblemDetail`: el handler responde `type` e `invalid_params`, no la
lista `detail` que FastAPI documenta por defecto.

**9.5** — Recorre `app/services/` y `app/api/` con `ast`; falla si encuentra `import beanie|motor|pymongo`
en `services/`, o `import ...models` en `api/`. Convierte la regla de dependencias de SPEC §6 en algo
que el CI rompe si alguien la viola. SC-13. 🔴 **NO HECHO — marcada 🟢 por error.**

> Esta tarea estaba marcada verde en el tracking, pero `app/tests/test_architecture.py` **no existe** en
> el repo: no hay ningún commit que lo añada y la suite no tiene ningún test que recorra el árbol con
> `ast`. Lo verificado es que hoy la regla se respeta *de facto* (no hay `import beanie|motor|pymongo`
> en `services/` ni `import ...models` en `api/`), pero eso es una comprobación manual, no un test que
> el CI pueda romper. **SC-13 sigue sin cubrir automáticamente** hasta escribir el fichero. No se ha
> escrito aquí por no ampliar el alcance de 9.7/9.8 sin avisar.

**9.6** — 🟢 Hecho. `[tool.coverage.report].fail_under = 85` en `pyproject.toml`. Además se corrigió que
`app/main.py` estaba en la lista de `omit`: `create_app()` sí lo ejercita toda la suite de API, así que
omitirlo excluía de lameasurement justo el fichero que más conviene vigilar. Sólo queda omitido
`app/tests/*`. Estado real medido: **93.33 %** global y **100 %** en `app/exceptions/` y
`app/schemas/problem.py`, que es lo que SC-17 exige.

**9.7** — 🔵 Escrito, **sin ejecutar**. `astral-sh/setup-uv` + `actions/checkout`; matriz Python
3.11/3.12/3.13. Job `lint-and-unit` (`ruff check`, `ruff format --check`, `mypy app`,
`pytest -m "not integration"` + cobertura, **sin Docker**), job `integration` (Mongo vía Testcontainers)
y job `docker-build`. SC-20.

> **El workflow no se ha ejecutado todavía**: no hay Docker en esta máquina, así que el job
> `integration` sólo puede validarse en GitHub. No marcar SC-16/SC-20 como verdes hasta que el primer
> run en GitHub Actions pase.

Tres decisiones que no están en el enunciado y conviene que se lean antes de tocar el fichero:

- **Dos jobs de cobertura, no uno.** El 85 % global va en `fail_under` de `pyproject.toml`, pero el 100 %
  de `app/exceptions/` y `app/schemas/problem.py` se comprueba en un paso aparte del workflow:
  `--cov-fail-under` no puede expresar «estos dos ficheros al 100 % y el resto al 85 %».
- **`uv sync --frozen`, no `uv sync`.** Con `--frozen`, si `uv.lock` no cuadra con `pyproject.toml` el
  comando falla en vez de re-resolver y escribir un lock nuevo en el runner. Lo que se prueba es lo que
  está commiteado.
- **El job `integration` no depende del de lint.** No lleva `needs:` a propósito: MongoDB no depende de
  que el código esté formateado, y encadenarlos haría que un error de estilo retrasara la única señal
  que importa en ese job, que es si el índice único rechaza el duplicado de verdad.

**9.8** — 🔵 Hecho. README con: qué es y qué no hace el servicio, arquitectura en 3 capas con el criterio
de por qué `schemas/` y `exceptions/` viven fuera de las capas, tabla de endpoints con sus errores,
ejemplo `curl` de cada uno, contrato RFC 9457 con la regla 400/415/422 y por qué `invalid_params` no se
aplana, `uv sync` + ejecución local, Docker, calidad y CI, configuración, y la **advertencia de que no
hay autenticación** (SPEC §1.1 D-3) con los cuatro puntos a hacer antes de exponerlo.

> Cada ruta, fichero, URN, comando, variable de entorno y status del README se contrastó por script contra
> el OpenAPI, `problem_types`, `document.py`, `config.py` y el árbol real de `app/`. Los ejemplos `curl`
> se ejecutaron contra la app con `FakePdfRepository`; los cuerpos de respuesta del README son salidas
> reales. Pendiente de **revisión humana**: si algún texto no coincide con lo que tu profesor espera
> mantener, es el fichero a tocar.

---

## Trazabilidad spec → tareas

| SC | Tarea que lo verifica |
|---|---|
| SC-01 | 4.2 🔵 |
| SC-02 | 4.1 + 4.2 🔵 |
| SC-03 | 3.1 + 3.4 🔵 **(G1)** |
| SC-04 | 7.1 + 7.2 🔵 |
| SC-05 | 6.1 + 6.2 🔵 |
| SC-06 | 5.1 + 5.2 🔵 |
| SC-07 | 9.1 (con `tz_aware=True`) + 9.3 🔵 |
| SC-08 | 8.1 + 8.2 🔵 |
| SC-09 | 2.3 + 9.3 🟢 |
| SC-10 | 2.1 + 9.3 🟢 |
| SC-11 | 3.5 + 9.3 🟢 |
| SC-12 | 3.5 + 9.3 🟢 |
| SC-13 | 9.5 🔴 **falta el fichero** |
| SC-14 | 0.2 🟢 |
| SC-15 | 0.2 + 3.2 🟢 |
| SC-16 | 9.7 (CI) 🔵 **sin ejecutar** |
| SC-17 | 2.2 + 9.6 🟢 |
| SC-18 | 0.1 🟢 |
| SC-19 | 9.1 + 9.3 (media type del OpenAPI) 🟢 |
| SC-20 | 9.7 🔵 **sin ejecutar** |
| SC-21 | 9.3 + 9.4 🟢 |
| SC-22 | 3.5 + 9.3 🟢 |
