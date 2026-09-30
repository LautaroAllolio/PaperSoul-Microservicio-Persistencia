# Spec: PaperSoul — Microservicio de Persistencia de Documentos PDF

> **Estado:** aprobado por el Tech Lead (2026-09-29). Documento vivo: si cambia una decisión,
> se actualiza esta spec **antes** de cambiar el código.
> **Alcance:** Fase 1 (Specify). Plan técnico en [`tasks/plan.md`](tasks/plan.md);
> checklist de ejecución y estrategia de Git en [`tasks/todo.md`](tasks/todo.md).

---

## 1. Objetivo

Microservicio REST que **persiste y consulta documentos PDF ya procesados** por un orquestador
upstream (`Extraer`), garantizando que un mismo PDF nunca se procese dos veces.

**Consumidores:**

| Consumidor | Dirección | Necesidad |
|---|---|---|
| Orquestador `Extraer` | Productor | Enviar metadata + texto extraído; verificar por hash antes de reprocesar |
| Microservicio `Validar` | Consumidor | Listar y leer documentos para validarlos |
| Operador | Humano | Explorar, depurar y eliminar documentos vía OpenAPI/Swagger |

**Historias de usuario:**

- **US-1** — Como orquestador, quiero `GET /by-hash/{pdf_hash}` para consultar si un PDF ya fue
  persistido **sin recibir un error** cuando no existe, para no gastar CPU reextrayendo.
- **US-2** — Como orquestador, quiero `POST /documents` que rechace con **409** un `pdf_hash`
  duplicado, para mantener la integridad incluso ante reintentos concurrentes.
- **US-3** — Como consumidor, quiero listar documentos ordenados de más nuevo a más viejo con
  paginación estable, para recorrer el corpus reciente.
- **US-4** — Como operador, quiero eliminar un documento por ID y recibir **404** claro si no existe.
- **US-5** — Como cualquier consumidor, quiero que **todo** error sea `application/problem+json`
  (RFC 9457), para parsear el fallo de forma genérica sin conocer este servicio.

**Definición de "documento persistido"** — el contrato pactado con el orquestador (compatibilidad con
el monolito), exactamente 7 campos:

| Campo | Tipo | Regla |
|---|---|---|
| `filename` | `str` | No vacío, ≤ 255 chars |
| `extracted_text` | `str` | No vacío, ≤ 10 000 000 chars (ver §1.1) |
| `extraction_method` | `"pymupdf" \| "ocr"` | Enum cerrado |
| `page_count` | `int` | ≥ 1 |
| `pdf_hash` | `str` | **SHA-256, 64 hex en minúsculas**, índice único |
| `text_hash` | `str \| null` | SHA-256, 64 hex en minúsculas; opcional |
| `uploaded_at` | `datetime` | UTC. El servidor lo genera si el payload no lo trae |

> El campo `status` de la respuesta `201` (`"persisted"`) es una **constante del contrato de
> transporte**, no un campo del documento. No se persiste. Ver §5.1.

### 1.1 Decisiones cerradas (confirmadas por el Tech Lead)

| Decisión | Valor | Consecuencia técnica |
|---|---|---|
| **D-1** Algoritmo y formato del hash | SHA-256, hexadecimal, **exactamente 64 caracteres en minúsculas**. `pdf_hash` y `text_hash` siguen el mismo formato. | El patrón es `^[0-9a-f]{64}$` — **sin flag `i`**. El uppercase se rechaza con 422 en vez de normalizarse en silencio: un `pdfHash` en mayúsculas es un bug del productor, y normalizarlo escondería ese bug justo en el campo que garantiza la deduplicación |
| **D-2** `Content-Type` incorrecto | **415 Unsupported Media Type**, no 400 | Desviación consciente del listado del enunciado. RFC 9110 §15.5.16 reserva 415 para "el formato de los datos no es soportado por el servidor"; 400 significaría que el servidor *entendió* el `Content-Type` y lo rechazó, que es falso. **Riesgo**: si `Extraer` tiene un cliente HTTP mal configurado, 415 es menos intuitivo que 400 al depurar |
| **D-3** Autenticación | **Ninguna.** Red interna de microservicios | La API no implementa auth, ni tokens, ni mTLS. **Consecuencia**: el servicio no puede exponerse fuera de la red privada sin añadir autenticación primero. Documentado en `docker-compose.yml` (`ports` loopback) y en el README como advertencia operativa |

### 1.2 Restricciones que imponen los datos

- **Límite BSON de 16 MB.** `extracted_text` vive dentro del mismo documento que el resto. Por eso se
  fija un techo de **10 000 000 caracteres**. Un PDF cuyo texto extraído lo supere recibe **422** en
  lugar de revienta por `DocumentTooLarge` del driver. Documentos mayores requerirían GridFS: fuera
  de alcance (YAGNI), pero el techo deja la puerta abierta.
- **Precisión temporal.** BSON almacena `datetime` con resolución de **milisegundos** y **descarta el
  tzinfo**. El cliente Mongo se crea con `tz_aware=True` para que la lectura devuelva UTC *aware* y la
  respuesta HTTP serie como `...Z` / `+00:00`. Sin esto, el mismo documento respondería con y sin
  offset según la ruta de acceso. Es criterio de aceptación explícito (§9, SC-07).

---

## 2. Tech Stack

Versiones **verificadas contra PyPI el 2026-09-29**. No son Estimate: son las reales.

| Paquete | Versión | Nota |
|---|---|---|
| Python | `>=3.11,<3.14` | `.python-version` → `3.11`. `StrEnum` exige 3.11+ |
| `uv` | `0.11.3` | Gestor de proyecto y lockfile |
| `fastapi` | `0.142.1` | Genera OpenAPI **3.1.0** por defecto |
| `uvicorn[standard]` | `0.54.0` | Servidor ASGI |
| `pydantic` | `2.13.5` | v2 estricto |
| `pydantic-settings` | `2.15.0` | Config por entorno |
| **`beanie`** | **`1.30.0` — pin EXACTO** | ⚠️ Ver §2.1 |
| `motor` | `3.7.1` | Driver async (EOL, ver §2.1) |
| `pytest` | `9.1.1` | |
| `pytest-asyncio` | `1.4.0` | API 1.x: sin fixture `event_loop` |
| `pytest-cov` | `7.1.0` | |
| `httpx` | `0.28.1` | Cliente de tests async |
| `asgi-lifespan` | `2.1.0` | Dispara `lifespan` en tests |
| `testcontainers[mongo]` | `4.15.0` | MongoDB real en Docker |
| `ruff` | `0.16.9` | Lint + format |
| `mypy` | latest | Type check |

### 2.1 Decisiones de stack que requieren justificación explícita

**`beanie==1.30.0` está pinado exacto, sin caret.** Beanie 2.0 (2025-07-20) eliminó Motor por completo
y migró a `pymongo.AsyncMongoClient`. Un `beanie>=1.30` resolvería a 2.2.0 y rompería el import de
Motor en el primer arranque. El pin no es estética: es la única cosa que mantiene coherente el stack
elegido.

**Motor está en end-of-life.** MongoDB lo deprecó el 2025-05-14, el EOL oficial fue el 2026-05-14
(hoy ya pasó) y sólo recibe fixes críticos hasta el 2027-05-14. Beanie 1.30 sigue siendo la última
serie sobre Motor. **Ésta es una decisión consciente, no un descuido**, y su consecuencia operativa es
una migración futura planificada:

> **Escapatoria:** al migrar, sólo cambian `app/core/database.py` y los `Document`
> (`get_motor_collection` → `get_pymongo_collection`). `app/services/` y `app/api/` no se tocan,
> porque la dependencia del servicio apunta al ABC del repositorio, no a Beanie (§6.2).
> Ésta es la razón de ser del patrón Repository en este servicio.

**`mongomock-motor` queda descartado.** Versiona sobre `motor>=2.5` y emula `AsyncIOMotorClient`.
Con Beanie 1.30 **funcionaría**, pero es un mock de "mejor esfuerzo" que no replica
`create_index` con unicidad real: no puede verificar que el índice `pdf_hash` exista ni que
`DuplicateKeyError` salte de verdad. Los dos comportamientos que US-2 depende no son verificables con
él. Se usan contenedores reales.

### 2.2 Estado del entorno de desarrollo (verificado 2026-09-29)

| Herramienta | Estado |
|---|---|
| `uv` 0.11.3 | ✅ Instalado |
| Python del sistema | 3.12.3 (uv descargará 3.11 según `.python-version`) |
| **Docker / Docker Compose** | ❌ **NO instalado en esta máquina** |

**Impacto:** `uv run pytest -m "not integration"` funciona ya. `uv run pytest` completo y
`docker compose up -d mongo` **no** funcionarán hasta instalar Docker Desktop. Los tests de integración
no se pueden verificar localmente hasta entonces; en CI (GitHub Actions, `ubuntu-latest`) Docker sí
está disponible. **Acción requerida del equipo: instalar Docker Desktop antes del lote S3.**

---

## 3. Commands

```bash
# --- Entorno ---
uv sync                      # instala deps + grupo dev, respeta uv.lock
uv run python -V             # 3.11.x

# --- Ejecutar ---
uv run uvicorn app.main:app --reload --port 8000
uv run uvicorn app.main:app --host 0.0.0.0 --port 8000   # sin reload (contenedor)

# --- Base de datos local (requiere Docker) ---
docker compose up -d mongo
docker compose down -v

# --- Calidad (gate de pre-push) ---
uv run ruff check .
uv run ruff format --check .
uv run mypy app

# --- Tests ---
uv run pytest -m "not integration"                    # rápido, SIN Docker
uv run pytest                                        # todo (requiere Docker)
uv run pytest tests/api/test_documents_api.py -v     # un archivo
uv run pytest --cov=app --cov-report=term-missing    # con cobertura

# --- OpenAPI ---
uv run python -c "import json,urllib.request as u; \
  print(json.dumps(json.load(u.urlopen('http://127.0.0.1:8000/openapi.json')),indent=2))"
```

---

## 4. Project Structure

```
PaperSoul-Microservicio-Persistencia/
├── pyproject.toml            # PEP 621 + [tool.ruff]/[tool.pytest]/[tool.mypy]
├── uv.lock                   # COMMITTED (reproducibilidad)
├── .python-version           # 3.11
├── .env.example              # Plantilla; .env NUNCA se commitea
├── .gitignore                # extendido: .venv, .env, .ruff_cache, .pytest_cache
├── Dockerfile
├── docker-compose.yml        # MongoDB 7 + Mongo Express (dev, puertos en loopback)
├── SPEC.md                   # ← este documento
├── tasks/
│   ├── plan.md               # Fase 2: diseño técnico
│   └── todo.md               # Fase 3: checklist atómico + Git
├── .github/workflows/ci.yml
└── app/
    ├── main.py                       # create_app() + lifespan + metadata OpenAPI
    ├── api/                          # CAPA 1 — PRESENTACIÓN / TRANSPORTE
    │   ├── v1/
    │   │   ├── router.py             # Agregador, prefija /api/v1
    │   │   └── documents.py          # Los 5 endpoints
    │   ├── dependencies.py           # Overrides de FastAPI → composition root
    │   ├── errors.py                 # Exception handlers → application/problem+json
    │   └── health.py
    ├── services/                     # CAPA 2 — NEGOCIO
    │   └── document_service.py       # Casos de uso, reglas, orquestación
    ├── repositories/                 # CAPA 3 — DATOS
    │   ├── base.py                   # BaseRepository[T] (contrato genérico)
    │   └── pdf_repository.py         # PdfRepository (contrato) + BeaniePdfRepository (impl)
    ├── models/                       # Documentos Beanie (modelo de persistencia)
    │   └── pdf_document.py
    ├── schemas/                      # DTOs Pydantic (frontera de transporte)
    │   ├── document.py
    │   ├── problem.py                # RFC 9457 ProblemDetail + InvalidParam
    │   ├── pagination.py
    │   └── mappers.py                # Beanie Document ↔ Pydantic schema
    ├── exceptions/                   # Excepciones de dominio
    │   ├── base.py                   # PaperSoulError
    │   └── domain.py                 # NotFound / Duplicate / InvalidIdentifier
    ├── core/
    │   ├── config.py                 # Settings (pydantic-settings)
    │   ├── container.py              # Composition root / IoC
    │   ├── database.py               # Motor client + init_beanie
    │   ├── problem_types.py          # URNs de `type`
    │   └── logging.py
    └── tests/
        ├── conftest.py               # Fixtures: container, lifespan, client, fakes
        ├── fakes.py                  # FakePdfRepository en memoria
        ├── unit/
        ├── integration/
        ├── api/
        └── test_architecture.py      # AST: verifica la regla de dependencias
```

**Por qué `schemas/` es una capa y no parte de `api/`:** los DTOs son el contrato público hacia el
orquestador. Si vivieran dentro de `api/`, el servicio no podría tipar sus retornos sin importar la
capa de transporte, y la regla de dependencias se rompe. Igual que `models/` es el contrato hacia
MongoDB y vive aparte de `repositories/`.

**Por qué `exceptions/` está al nivel de `app/`:** las lanzan los servicios y las atrapan los handlers
de `api/`. Si vivieran en `api/`, la capa de negocio importaría hacia arriba. Es la capa transversal
de este diseño.

---

## 5. Contrato de API (OpenAPI 3.1)

Base: `https://papersoul.invalid` — **cabecera `Accept: application/json, application/problem+json` en
todas las peticiones de error.**

### 5.1 Esquemas

#### `DocumentCreateRequest`

```yaml
DocumentCreateRequest:
  type: object
  required: [filename, extracted_text, extraction_method, page_count, pdf_hash]
  additionalProperties: false
  properties:
    filename:
      type: string
      minLength: 1
      maxLength: 255
      examples: ["contrato-2026.pdf"]
    extracted_text:
      type: string
      minLength: 1
      maxLength: 10000000          # Techo por el límite BSON de 16 MB (§1.2)
    extraction_method:
      type: string
      enum: ["pymupdf", "ocr"]
    page_count:
      type: integer
      minimum: 1
    pdf_hash:
      type: string
      # SHA-256 hex minúsculas, exactamente 64 chars (D-1).
      # Sin flag `i`: el uppercase se rechaza, no se normaliza.
      pattern: "^[0-9a-f]{64}$"
      examples: ["e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"]
    text_hash:
      type: [string, "null"]
      pattern: "^[0-9a-f]{64}$"
      default: null                # mismo formato que pdf_hash (D-1)
    uploaded_at:
      type: [string, "null"]
      format: date-time
      default: null                # null ⇒ el servidor asigna datetime.now(UTC)
```

`additionalProperties: false` es deliberado: un `pdf_hash` mal escrito que viaje como `pdfHash`
fallaría en 422 en vez de guardarse silenciosamente sin índice.

#### `DocumentResponse`

```yaml
DocumentResponse:
  type: object
  required: [id, filename, extracted_text, extraction_method, page_count, pdf_hash, uploaded_at]
  properties:
    id: { type: string, examples: ["665f1c9e8a1b2c3d4e5f6071"] }   # ObjectId hex, 24 chars
    filename: { type: string }
    extracted_text: { type: string }
    extraction_method: { type: string, enum: ["pymupdf", "ocr"] }
    page_count: { type: integer }
    pdf_hash: { type: string }
    text_hash: { type: [string, "null"] }
    uploaded_at: { type: string, format: date-time }             # SIEMPRE con offset UTC
```

#### `DocumentPersistedResponse` (respuesta del 201)

```yaml
DocumentPersistedResponse:
  type: object
  required: [id, status]
  properties:
    id: { type: string }
    status: { type: string, enum: ["persisted"] }   # Constante, no campo persistido
```

#### `DocumentListResponse`

```yaml
DocumentListResponse:
  type: object
  required: [items, total, limit, offset]
  properties:
    items: { type: array, items: { $ref: "#/components/schemas/DocumentResponse" } }
    total:  { type: integer, minimum: 0 }   # cost: 1 count query adicional (§10, OT-6)
    limit:  { type: integer, minimum: 1, maximum: 100 }
    offset: { type: integer, minimum: 0 }
```

#### `HashCheckResponse` (respuesta del `by-hash`)

```yaml
HashCheckResponse:
  type: object
  required: [exists]
  properties:
    exists:      { type: boolean }
    id:          { type: [string, "null"] }
    uploaded_at: { type: [string, "null"], format: date-time }
```

> Semántica: **siempre 200**, exista o no. Cuando `exists: false`, `id` y `uploaded_at` son `null`.
> Un 404 obligaría al orquestador a capturar excepciones en el camino caliente (§10, OT-2).

#### `ProblemDetail` (RFC 9457)

```yaml
ProblemDetail:
  type: object
  description: |
    RFC 9457 "Problem Details for HTTP APIs". Los miembros extension
    (`invalid_params`, `trace_id`) van en la raíz; RFC 9457 los permite.
  required: [type, title, status]
  properties:
    type:
      type: string
      format: uri-reference
      default: "about:blank"
      description: URN estable. NUNCA cambia: es la clave de Programmatic de los clientes.
    title:  { type: string, description: "Resumen corto, estable, NO localizado" }
    status: { type: integer, description: "Reproduce el status HTTP" }
    detail: { type: string, description: "Explicación de ESTA ocurrencia, en español" }
    instance: { type: string, format: uri-reference, description: "URI de la ocurrencia" }
    invalid_params:
      type: [array, "null"]
      description: Extensión: causa del 422 de validación, con `loc` fiel.
      items:
        type: object
        required: [loc, msg, type]
        properties:
          loc:
            type: array
            description: |
              Ruta del campo. Preserva índices de listas: ["body","extracted_text",3]
            items: { anyOf: [{ type: string }, { type: integer }] }
          msg:  { type: string }
          type: { type: string, description: "Slug de Pydantic, p.ej. string_too_long" }
    trace_id:
      type: [string, "null"]
      description: Extensión: correlaciona el error con el log del servidor
  additionalProperties: true
```

### 5.2 Endpoints

| # | Método | Ruta | Éxito | Errores |
|---|---|---|---|---|
| 1 | `POST` | `/api/v1/documents` | `201` `DocumentPersistedResponse` | 400, 409, 415, 422, 500 |
| 2 | `GET` | `/api/v1/documents` | `200` `DocumentListResponse` | 400, 415, 422, 500 |
| 3 | `GET` | `/api/v1/documents/{doc_id}` | `200` `DocumentResponse` | 400, 404, 500 |
| 4 | `GET` | `/api/v1/documents/by-hash/{pdf_hash}` | `200` `HashCheckResponse` | 400, 415, 422, 500 |
| 5 | `DELETE` | `/api/v1/documents/{doc_id}` | `204` (sin cuerpo) | 400, 404, 500 |
| — | `GET` | `/health` | `200` (liveness/readiness) | 500 |

> **Orden de declaración importa.** `by-hash` se declara **antes** que `{doc_id}`. No es un conflicto
> real (`/documents/by-hash/abc` tiene 2 segmentos, `/documents/{doc_id}` tiene 1), pero declararlo
> primero hace la intención explícita y sobrevive a futuros cambios de rutas.

> **`Content-Type` sólo se exige donde hay body.** En GET/DELETE se acepta su ausencia (no llevan
> cuerpo) y se rechaza cualquier `Content-Type` explícito que no sea JSON. En POST es obligatorio
> `application/json`; sin él, o con `text/plain`, la respuesta es 415 (SC-21).

**`POST /api/v1/documents`** — `201`, `Location: /api/v1/documents/{id}`

```json
{ "id": "665f1c9e8a1b2c3d4e5f6071", "status": "persisted" }
```

**`GET /api/v1/documents`** — query: `limit` (1..100, def. 20), `offset` (≥0, def. 0)
Orden: **`uploaded_at` DESC, `_id` DESC**. El `_id` es desempate obligatorio: sin él, dos documentos
con el mismo milisegundo pueden repetirse o perderse al paginar (BSON trunca a ms, §1.2).

```json
{ "items": [ { "id": "...", "filename": "contrato-2026.pdf", "...": "..." } ],
  "total": 42, "limit": 20, "offset": 0 }
```

**`GET /api/v1/documents/{doc_id}`** — `200` `DocumentResponse`; `404` si no existe.

**`GET /api/v1/documents/by-hash/{pdf_hash}`** — `200` siempre:

```json
{ "exists": true,  "id": "665f1c9e8a1b2c3d4e5f6071", "uploaded_at": "2026-09-29T18:30:00Z" }
{ "exists": false, "id": null, "uploaded_at": null }
```

**`DELETE /api/v1/documents/{doc_id}`** — `204` sin cuerpo. `404` si no existe.

### 5.3 Taxonomía de errores (RFC 9457)

| Situación | Status | `type` (URN) | `title` |
|---|---|---|---|
| JSON malformado | `400` | `urn:problem:papersoul:malformed-json` | Solicitud malformada |
| `doc_id` no es un ObjectId (≠24 hex) | `400` | `urn:problem:papersoul:invalid-document-id` | Identificador de documento inválido |
| `Content-Type` ≠ `application/json` | **`415`** | `urn:problem:papersoul:unsupported-media-type` | Tipo de medio no soportado |
| Falla validación de Pydantic | `422` | `urn:problem:papersoul:request-validation-failed` | Solicitud no procesable |
| Documento no existe | `404` | `urn:problem:papersoul:document-not-found` | Documento no encontrado |
| `pdf_hash` duplicado | `409` | `urn:problem:papersoul:document-hash-conflict` | Conflicto de documento |
| Excepción no controlada | `500` | `urn:problem:papersoul:internal-error` | Error interno del servidor |

Todos incluyen `instance` = `request.url.path` y `trace_id` (UUID de correlación, también en el log).

**El corte 400 / 422 es semántico, no arbitrario:**

- **`400`** → la petición es *intransmisible o ambigua*: JSON que no parsea, un ID con formato
  imposible. Ningún payload válido la evita.
- **`415`** → la petición es *entendible pero el formato de datos no está soportado*. Desviación
  deliberada del enunciado, justificada en §1.1 (D-2).
- **`422`** → la petición *se entiende* pero viola las reglas de negocio declaradas en el schema
  (`minLength`, `enum`, `minimum`, `maxLength`, `pattern`, campos obligatorios). Un cliente bien
  formado la evita.

**`invalid_params` no se aplana.** `loc` conserva índices: un error en el tercer elemento de una lista
reporta `["body", "items", 2, "field"]`, no `["body", "items", "field"]`. Sin esto, un cliente no puede
saber *qué* elemento falló.

**`title` es estable y no se localiza; `detail` sí, y está en español.** Un cliente que cambia de
idioma debe poder comparar `title` y hacer `switch` sobre `type` sin parsing frágil. `detail` es
diagnóstico: puede cambiar sin romper a nadie.

---

## 6. Code Style

**Normas:** snake_case · Tipos explícitos en toda firma pública · `async def` sin `await` en la
declaración · early return, nunca `else` tras `return` · `enum` sobre strings mágicos · Docstring sólo
cuando el *qué* no es obvio (el *por qué* va en comment).

**Ejemplo de referencia — la jerarquía de errores de dominio.** Del dominio al transporte, sin que la
capa de negocio conozca FastAPI:

```python
# app/exceptions/base.py
class PaperSoulError(Exception):
    """Raíz de todo error de dominio. El handler HTTP sólo conoce esta clase."""

    status_code: int = 500
    problem_type: str
    title: str

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.detail = detail

# app/exceptions/domain.py
class ResourceNotFoundException(PaperSoulError):
    status_code = 404
    problem_type = ProblemType.DOCUMENT_NOT_FOUND
    title = "Documento no encontrado"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)
        self.resource_name = "document"   # miembro extension RFC 9457
```

```python
# app/api/errors.py — el handler no conoce los tipos concretos, sólo la base
async def paper_soul_error_handler(request: Request, exc: PaperSoulError) -> JSONResponse:
    problem = ProblemDetail(
        type=exc.problem_type,
        title=exc.title,
        status=exc.status_code,
        detail=exc.detail,
        instance=str(request.url.path),
        trace_id=new_trace_id(),
    )
    return JSONResponse(status_code=exc.status_code, content=problem.model_dump(exclude_none=True),
                        media_type=PROBLEM_JSON)
```

**Por qué así y no de otra forma:** añadir un error nuevo es añadir *una* clase en `domain.py` con
dos atributos. **Ni el handler ni el router cambian.** Un `except ResourceNotFoundException` nuevo en
`api/errors.py` habría acoplado el transporte a cada caso de negocio — es exactamente el
"shotgun surgery" que la regla de dependencias evita.

**Frontera de capas (regla de dependencias):**

```
api  ──►  services  ──►  repositories (ABC)
 │            │                  │
 │            └──► schemas        └──► models  ──►  beanie
 └──►  schemas, exceptions, core
```

Flechas apuntan **hacia adentro**. Prohibido: `services → beanie`, `api → models`,
`schemas → services`. Se verifica mecánicamente en `app/tests/test_architecture.py` (§8.3).

---

## 7. Boundaries

### Siempre
- `uv run ruff check . && uv run ruff format --check . && uv run mypy app` antes de cada commit.
- `uv run pytest` verde antes de `git push`. Sin excepción, ni "ya lo arreglo después".
- Escribir el test que falla **antes** del código (rojo → verde → refactor), un corte vertical por vez.
- Un cambio de contrato toca `SPEC.md` **primero**.
- `type` URNs y `title` son API pública: cambiar uno es breaking change.
- Un `commit` = una tarea de `tasks/todo.md`. Nunca mezclar refactor con feature.

### Preguntar primero
- Cambiar el esquema de `PdfDocument` o añadir/quitar índices (afecta a datos ya persistidos).
- Tocar `pyproject.toml` / `uv.lock` / `.github/workflows/ci.yml`.
- Añadir dependencia — en particular `fastapi-rfc9457` (ver OT-1).
- Cambiar cualquier campo de `DocumentCreateRequest` o `DocumentResponse`: rompe el contrato con `Extraer`.
- Cambiar de Beanie 1.30 a 2.x (obliga a la migración a PyMongo Async, §2.1).
- Subir `maxLength` de `extracted_text` (obliga a rediseñar el almacenamiento).

### Nunca
- Commitar secretos, `.env`, ni credenciales de Mongo en la URI. Sólo `.env.example`.
- Tocar `app/tests/` para hacer pasar un test. Un test que estorba se reescribe **con el orquestador
  de por medio**, no se borra.
- Instalar paquetes con `pip install` en el shell global. Todo por `uv add`.
- Quitar `beanie==1.30.0` del pin (§2.1).
- Loguear `extracted_text` completo: puede contener datos sensibles de un contrato.
- Mergear a `main` sin `main` verde en CI.
- Exponer el servicio fuera de la red privada: **no hay autenticación** (§1.1, D-3).

---

## 8. Testing Strategy

Framework: **pytest 9** + **pytest-asyncio 1.4** (`asyncio_mode = "auto"`,
`asyncio_default_fixture_loop_scope = "function"`).

### 8.1 Los tres niveles

| Nivel | Ubicación | Qué cubre | DB |
|---|---|---|---|
| **Unit** | `tests/unit/` | Servicio, mappers, `ProblemDetail` | Fakes en memoria |
| **Integración** | `tests/integration/` | `BeaniePdfRepository` real: índices, `DuplicateKeyError`, orden, paginación | Testcontainers |
| **API** | `tests/api/` | Los 5 endpoints, `Content-Type`, status, cuerpo | Testcontainers (lifespan real) |

**Por qué el servicio se testea con un fake y no contra Mongo.** US-2 tiene dos niveles que un test
puede comprobar: (a) *el servicio traduce `DuplicateKeyError` a 409* y (b) *el índice único
realmente rechaza el duplicado*. (b) sólo se prueba con Mongo real. (a) es lógica de negocio pura y con
Mongo real costaría un segundo por aserción sin probar nada más. Se separan: la integración valida el
índice, la unidad valida la traducción.

### 8.2 Fakes, no mocks

`tests/fakes.py` implementa `PdfRepository` con un `dict` en memoria, **incluida la unicidad de
`pdf_hash`**. Se escriben a mano, no con `unittest.mock`: un `Mock` que devuelve lo que el test espera
pasa aunque el código real esté roto (test tautológico); un fake con la regla de unicidad implementada
de verdad puede **fallar**.

### 8.3 Trampas de pytest-asyncio resueltas de antemano

Estas tres rompen la suite silenciosamente si no se tienen en cuenta. Están resueltas en
`tests/conftest.py`, no descubiertas en CI:

1. **`RuntimeError: Task attached to a different loop`.** Es el fallo nº 1 con Beanie. Causa: un fixture
   `scope="session"` con cliente Mongo atado a un loop, reutilizado desde loops de función. *Mitigación:*
   el contenedor es `session`-scope pero **sólo expone una URI (string)** — nunca un objeto de driver.
   `init_beanie` corre por función, en el loop de esa función.
2. **`CollectionWasNotInitialized`.** `init_beanie` no se llamó para ese modelo. *Mitigación:* fixture
   `beanie_client` **autouse** que llama `init_beanie` con `PdfDocument`.
3. **Lifespan no ejecutado.** `httpx.ASGITransport` **no** dispara `lifespan`. Sin él, la app arranca
   sin Mongo y todo falla con errores confusos. *Mitigación:*
   `LifespanManager(app)` de `asgi-lifespan` envolviendo cada cliente de test.

**Cliente de API:** `httpx.AsyncClient(transport=ASGITransport(app=app))` **dentro** de un test async,
con lifespan gestionado. Todo el stack de test es async y comparte **un solo event loop** por test.

### 8.4 Aislamiento

Cada test usa una **base de datos con nombre único** (`papersoul_test_{uuid4().hex[:8]}`) y la borra al
terminar. Orden de tests irrelevante, y `pytest -k` funciona.

### 8.5 Cobertura

`pytest-cov`, objetivo **≥ 85 %** en `app/`, y **100 %** en `app/exceptions/` y `app/schemas/problem.py`
— código puro de contrato, sin excusas para dejarlo sin cubrir.

### 8.6 Marcadores

`@pytest.mark.integration` en todo lo que necesita Mongo. `uv run pytest -m "not integration"` corre en
segundos sin Docker (ideal para el loop TDD y para esta máquina, que no tiene Docker, §2.2).

---

## 9. Success Criteria

Cada criterio es binario y verificable con un comando.

| ID | Criterio | Verificación |
|---|---|---|
| SC-01 | `POST` persiste un documento válido y responde `201` con `{"id","status":"persisted"}` y cabecera `Location` | `pytest tests/api -k test_post_returns_201` |
| SC-02 | `POST` con `pdf_hash` ya existente responde `409` + `type: ...document-hash-conflict` | `pytest -k test_duplicate_hash_returns_409` |
| SC-03 | El índice único `pdf_hash` existe **en Mongo real** y `get_motor_collection().index_information()` lo reporta con `unique: true` | `pytest -k test_pdf_hash_index_is_unique` |
| SC-04 | `GET /by-hash/{h}` responde `200` con `exists:false, id:null, uploaded_at:null` cuando no existe — **no** 404 | `pytest -k test_by_hash_not_found_returns_200` |
| SC-05 | `GET /documents` pagina y devuelve primero el más nuevo; con `uploaded_at` repetido, `_id` desempata sin repetir ni perder ítems entre páginas | `pytest -k test_list_pagination_is_stable` |
| SC-06 | `GET /{id}` con ObjectId válido inexistente → `404`; con id no-ObjectId → `400` (no 422, no 500) | `pytest -k "test_get_by_id"` |
| SC-07 | `uploaded_at` vuelve de Mongo **con offset UTC** (`+00:00`/`Z`) en todas las respuestas, no naive | `pytest -k test_uploaded_at_keeps_utc_offset` |
| SC-08 | `DELETE` responde `204` sin cuerpo; segunda llamada → `404` | `pytest -k "test_delete"` |
| SC-09 | **Todo** error HTTP devuelve `Content-Type: application/problem+json` y cuerpo con `type`, `title`, `status`, `instance` | `pytest tests/api/test_errors_rfc9457.py` |
| SC-10 | El 422 incluye `invalid_params` con `loc` fiel, **preservando índices** de listas | `pytest -k test_validation_error_preserves_list_index` |
| SC-11 | Un payload con campo desconocido (`pdfHash` en vez de `pdf_hash`) → 422, no se persiste en silencio | `pytest -k test_unknown_field_returns_422` |
| SC-12 | Un `extracted_text` de 10 000 001 chars → 422 con `invalid_params`, no excepción del driver | `pytest -k test_text_too_long_returns_422` |
| SC-13 | La regla de dependencias se verifica mecánicamente: `app/services/` **no** importa `beanie`, `motor` ni `pymongo` | `pytest app/tests/test_architecture.py` |
| SC-14 | `uv run ruff check . && uv run ruff format --check . && uv run mypy app` sale con código 0 | ejecución literal |
| SC-15 | `uv run pytest -m "not integration"` pasa **sin Docker** | ejecución literal |
| SC-16 | `uv run pytest` completo pasa (Mongo vía Testcontainers) | ejecución literal |
| SC-17 | Cobertura ≥ 85 % en `app/`; 100 % en `app/exceptions/` y `app/schemas/problem.py` | `pytest --cov=app --cov-fail-under=85` |
| SC-18 | `uv.lock` commiteado; `uv sync` en máquina limpia reproduce el entorno | `uv sync` en CI |
| SC-19 | `/openapi.json` declara `openapi: 3.1.0` y documenta los 5 endpoints con sus respuestas `application/problem+json` | inspección de `/openapi.json` |
| SC-20 | CI en GitHub Actions corre lint + typecheck + unit en Python 3.11/3.12/3.13 e integración con Docker | badge verde |
| SC-21 | `POST` con `Content-Type: text/plain` (o ausente con body) → `415` + `type: ...unsupported-media-type` | `pytest -k test_unsupported_media_type_returns_415` |
| SC-22 | `pdf_hash` con mayúsculas, longitud ≠64, o caracteres no-hex → 422, **no** se persiste ni se normaliza | `pytest -k test_pdf_hash_must_be_lowercase_sha256` |

---

## 10. Open Questions

**Cerradas** (confirmadas por el Tech Lead, 2026-09-29):

| # | Pregunta | Decisión |
|---|---|---|
| ~~OT-2~~ | `by-hash` → 200 vs 404 | **200 con envelope `{exists, id, uploaded_at}`** |
| ~~OT-4~~ | Algoritmo y formato de `pdf_hash` | **SHA-256, hex minúsculas, 64 chars**, en minúsculas estrictas. `text_hash` idéntico (§1.1, D-1) |
| ~~OT-7~~ | `Content-Type` incorrecto → 400 vs 415 | **415**, desviación consciente justificada por RFC 9110 (§1.1, D-2) |
| ~~OT-8~~ | Autenticación | **Ninguna**; red interna (§1.1, D-3) |

**Abiertas** — ninguna bloquea la implementación:

| # | Pregunta | Propuesta por defecto | Impacto si cambia |
|---|---|---|---|
| OT-1 | ¿Reimplementar RFC 9457 a mano o usar `fastapi-rfc9457` 0.2.3? Decidido: **a mano** (§2). La lib da 422 estructurado y `type` dereferenciable gratis, pero es 0.2.x y añade dependencia. | A mano | Bajo. Si se cambia, sólo `api/errors.py` |
| OT-3 | `page_count ≥ 1`: ¿un PDF de 0 páginas es legal? Se asumió **no**. | `minimum: 1` | Bajo |
| OT-5 | ¿`text_hash` tiene índice? No por defecto: sólo se busca por `pdf_hash`. | Sin índice | Bajo. Índices de más ralentizan las escrituras |
| OT-6 | `total` cuesta una query extra por listado. ¿Se acepta? | Sí, se incluye | Bajo. Se quita si molesta el orquestador |
| OT-9 | `BaseRepository[T]` genérico sólo tiene un subtipo. Strictly es generalización especulativa, pero lo pide el enunciado. ¿Se conserva? | Se conserva (pedido explícito) | Bajo |
| OT-10 | Tests en `app/tests/` (como pide el enunciado) vs `tests/` en la raíz (convención más común). | `app/tests/` | Bajo |
| OT-11 | **Docker no está instalado** en la máquina de desarrollo (§2.2). ¿Quién lo instala y cuándo? | Docker Desktop antes del lote S3 | **Alto** — bloquea SC-02/03/05/16 localmente |

---

## 11. Historia de decisiones (ADR-índice)

| Decisión | Alternativa descartada | Motivo |
|---|---|---|
| `beanie==1.30.0` + `motor` | `beanie` 2.x + `pymongo` Async | Requisito del enunciado. Coste aceptado: driver en EOL, con ruta de escape aislada en `core/database.py` |
| Testcontainers sobre mongomock | `mongomock-motor` | El mock no replica índices únicos reales; US-2 no sería verificable |
| RFC 9457 a mano | `fastapi-rfc9457` | Requisito del enunciado + control del OpenAPI; evita dependencia 0.2.x |
| `by-hash` → 200 + `exists` | 404 | No obligar al orquestador a capturar excepciones en el camino caliente |
| Handlers genéricos sobre `PaperSoulError` | Un handler por excepción | Añadir un error no toca el handler (§6) |
| `tz_aware=True` en el cliente | Default `False` | Sin esto, `uploaded_at` pierde el offset al releerse (SC-07) |
| `_id` como segundo criterio de orden | Sólo `uploaded_at DESC` | Sin desempate la paginación puede repetir/perder con ms iguales |
| `additionalProperties: false` | Default permisivo | Detecta `pdfHash` mal nombrado en vez de guardarlo sin índice |
| `^[0-9a-f]{64}$` estricto | Aceptar mayúsculas y normalizar | Normalizar escondería un bug del productor en el campo que garantiza la deduplicación (§1.1, D-1) |
| 415 para `Content-Type` | 400 del enunciado | RFC 9110 §15.5.16: 415 = formato no soportado; 400 implicaría que el servidor entendió el Content-Type (§1.1, D-2) |
| Sin autenticación | JWT / mTLS | Red interna de microservicios; decisión del Tech Lead (§1.1, D-3) |
