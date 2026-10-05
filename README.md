# PaperSoul · Microservicio de Persistencia

Microservicio que **persiste y consulta documentos PDF ya procesados** por otro
servicio. Recibe el resultado de la extracción, lo guarda en MongoDB y responde a
consultas por identificador y por hash.

No extrae texto, no recibe el binario del PDF y no decide nada sobre el
orquestador: es la capa que recuerda.

> **Este servicio no tiene autenticación ni autorización.** Cualquiera que alcance
> el puerto 8000 puede leer y borrar documentos. Está pensado para una red interna y
> para entorno de desarrollo. Ver [Seguridad](#seguridad).

---

## Índice

- [Arquitectura](#arquitectura)
- [Contrato de la API](#contrato-de-la-api)
- [Errores: RFC 9457](#errores-rfc-9457)
- [Ejecución local](#ejecución-local)
- [Calidad y CI](#calidad-y-ci)
- [Configuración](#configuración)
- [Desarrollo](#desarrollo)
- [Seguridad](#seguridad)

---

## Arquitectura

Tres capas, con dependencias en un solo sentido: **los routers conocen al servicio,
el servicio conoce al repositorio, y nadie sube**.

```
   HTTP                    CAPA 1                CAPA 2              CAPA 3
  ───────    ────────────────────────────────────────────────────────────────
  cliente →  api/            →  services/        →  repositories/     →  MongoDB
             v1/documents       document_service    pdf_repository
             errors.py                            (contrato + Beanie)
             dependencies.py
             openapi_problem.py

             schemas/     DTOs Pydantic: el contrato público hacia el orquestador
             exceptions/  PaperSoulError: errores de dominio, sin FastAPI
             models/      PdfDocument (Beanie): el contrato hacia MongoDB
             core/        config, container, database, problem_types, logging
```

**Qué hay en cada capa y por qué está donde está:**

| Capa | Directorio | Responsabilidad | No sabe de |
|---|---|---|---|
| 1 · Presentación | `app/api/` | HTTP: enrutado, validación de transporte, status, mapeo a `ProblemDetail` | Mongo, Beanie, reglas de negocio |
| 2 · Negocio | `app/services/` | Casos de uso y orquestación | FastAPI, Mongo |
| 3 · Datos | `app/repositories/` | Persistencia; contrato (`PdfRepository`) e implementación (`BeaniePdfRepository`) | FastAPI, HTTP |

Dos carpetas que quedan **fuera** de las tres capas y por qué:

- **`app/schemas/`** — los DTOs son el contrato público hacia el orquestador. Si
  vivieran dentro de `api/`, el servicio no podría tipar sus retornos sin importar la
  capa de transporte. Igual que `models/` es el contrato hacia MongoDB y no vive
  dentro de `repositories/`.
- **`app/exceptions/`** — las lanzan los servicios y las atrapan los handlers de
  `api/`. Si vivieran en `api/`, la capa de negocio importaría hacia arriba.

### El grafo de dependencias es explícito

`create_app()` construye un `Container` y lo publica en `app.state`. Los routers lo
resuelven con `Depends` en el momento de atender cada petición:

```
create_app()                       app.state.container
     │                                    │
     └── build_container(repo) ───────────┤
                                          ▼
                          get_document_service(request) → DocumentService
```

Consecuencia práctica: **una app entera se monta con un fake y sin Docker**, que es
lo que permite que los 83 tests de API corran en local y en un runner sin base de
datos. El `lifespan` sólo gestiona el recurso (el cliente de Mongo); el contenedor no
necesita conexión.

---

## Contrato de la API

Base: `/api/v1`. OpenAPI 3.1 en `/docs`, documento en `/openapi.json`.

| # | Método | Ruta | Éxito | Errores |
|---|---|---|---|---|
| 1 | `POST` | `/api/v1/documents` | `201` + `Location` | 400, 409, 415, 422, 500 |
| 2 | `GET` | `/api/v1/documents` | `200` | 422, 500 |
| 3 | `GET` | `/api/v1/documents/{doc_id}` | `200` | 400, 404, 422, 500 |
| 4 | `GET` | `/api/v1/documents/by-hash/{pdf_hash}` | `200` siempre | 400, 415, 422, 500 |
| 5 | `DELETE` | `/api/v1/documents/{doc_id}` | `204` sin cuerpo | 400, 404, 422, 500 |
| — | `GET` | `/health` | `200` | — |

### `POST /api/v1/documents`

Persiste un documento que el orquestador ya procesó.

```bash
curl -i -X POST http://localhost:8000/api/v1/documents \
  -H 'Content-Type: application/json' \
  -d '{
    "filename": "contrato-2026.pdf",
    "extracted_text": "texto extraído del PDF",
    "extraction_method": "pymupdf",
    "page_count": 3,
    "pdf_hash": "'"$(printf 'contenido' | sha256sum | cut -d' ' -f1)"'"
  }'
```

```http
HTTP/1.1 201 Created
Location: /api/v1/documents/665f1c9e8a1b2c3d4e5f6071
Content-Type: application/json
```
```json
{
  "id": "665f1c9e8a1b2c3d4e5f6071",
  "status": "persisted",
  "pdf_hash": "e03726ed…",
  "uploaded_at": "2026-10-05T21:10:22.445539Z"
}
```

El cuerpo no devuelve `extracted_text` a propósito: puede tener 10 MB y el
orquestador acaba de enviarlo. La confirmación lleva `id` y `status`; `pdf_hash` y
`uploaded_at` se añaden porque ahorran una segunda petición para confirmar qué se
guardó.

**Body (`additionalProperties: false`)**

| Campo | Tipo | Regla |
|---|---|---|
| `filename` | `string` | 1..255 caracteres |
| `extracted_text` | `string` | 1..10 000 000 caracteres |
| `extraction_method` | `"pymupdf"` \| `"ocr"` | enum cerrado |
| `page_count` | `integer` | ≥ 1 |
| `pdf_hash` | `string` | `^[0-9a-f]{64}$`, **minúsculas** |
| `text_hash` | `string \| null` | mismo patrón; opcional |
| `uploaded_at` | `datetime \| null` | opcional; el servidor lo genera si no viene |

Un campo desconocido da **422**, no se descarta en silencio: un `pdfHash` mal
escrito significaría un documento guardado sin hash, es decir sin índice y sin
deduplicación.

`pdf_hash` en mayúsculas se **rechaza** en lugar de normalizarse. Normalizar
escondería un bug del productor justo en el campo que garantiza la deduplicación, que
es donde un bug debe verse.

### `GET /api/v1/documents/{doc_id}`

```bash
curl http://localhost:8000/api/v1/documents/665f1c9e8a1b2c3d4e5f6071
```

`200` con el documento completo, `404` si no existe, `400` si el id no tiene forma de
ObjectId.

### `GET /api/v1/documents/by-hash/{pdf_hash}`

Responde **siempre 200**, exista o no. «¿Está ya subido este PDF?» tiene respuesta
«no» con la misma naturalidad que «sí», y un 404 obligaría al cliente a tratar la
ausencia como un error.

```bash
curl http://localhost:8000/api/v1/documents/by-hash/e03726ed659d4557a...
```

```json
{ "exists": true,  "id": "665f1c9e8a1b2c3d4e5f6071", "uploaded_at": "2026-10-05T21:10:22.445539Z" }
{ "exists": false, "id": null, "uploaded_at": null }
```

### `GET /api/v1/documents`

```bash
curl 'http://localhost:8000/api/v1/documents?limit=20&offset=0'
```

`limit` de 1 a 100 (por defecto 20), `offset` ≥ 0. El techo de 100 es deliberado: sin
él, un `?limit=1000000` convertiría el endpoint en una extracción de la colección
entera, y el `total` en la única promesa de coste acotado.

Orden: `uploaded_at` descendente, `_id` descendente. El `_id` es desempate
obligatorio — BSON trunca los timestamps a milisegundos, y sin él dos documentos del
mismo milisegundo pueden repetirse o perderse al paginar.

```json
{
  "items": [ { "id": "…", "filename": "contrato-2026.pdf", "…": "…" } ],
  "total": 42, "limit": 20, "offset": 0
}
```

### `DELETE /api/v1/documents/{doc_id}`

```bash
curl -i -X DELETE http://localhost:8000/api/v1/documents/665f1c9e8a1b2c3d4e5f6071
```

`204` sin cuerpo. `404` si no estaba. La segunda llamada al mismo id da 404: el
borrado no es idempotente desde el punto de vista del status, y por eso `404` es la
respuesta correcta en lugar de otro `204`.

### `GET /health`

```json
{ "status": "ok" }
```

No consulta MongoDB a propósito: si lo hiciera, un Mongo lento provocaría reinicios
del proceso, que es lo contrario de lo que se quiere de una sonda de vida. Lo que
comprueba es que el proceso está vivo.

---

## Errores: RFC 9457

**Toda** respuesta de error es `Content-Type: application/problem+json`, incluido en
el OpenAPI, para que un cliente pueda parsear el fallo sin conocer este servicio.

```json
{
  "type": "urn:problem:papersoul:document-hash-conflict",
  "title": "Conflicto de documento",
  "status": 409,
  "detail": "ya existe un documento con pdf_hash e03726ed…",
  "instance": "/api/v1/documents",
  "trace_id": "3b8b0a36-cdbb-4f87-b185-e45a015e1db4"
}
```

| Situación | Status | `type` (URN) |
|---|---|---|
| JSON malformado | `400` | `…:malformed-json` |
| `doc_id` no es ObjectId | `400` | `…:invalid-document-id` |
| `Content-Type` ≠ `application/json` | `415` | `…:unsupported-media-type` |
| Falla la validación del schema | `422` | `…:request-validation-failed` |
| El documento no existe | `404` | `…:document-not-found` |
| `pdf_hash` duplicado | `409` | `…:document-hash-conflict` |
| Excepción no controlada | `500` | `…:internal-error` |

### Tres reglas que conviene conocer antes de integrar

**El corte 400 / 422 es semántico, no arbitrario.**

- **400** — la petición es *intransmible o ambigua*: JSON que no parsea, un id con
  formato imposible. Ningún payload válido la evita.
- **415** — la petición se entiende, pero el formato de datos no está soportado.
- **422** — la petición se entiende pero viola las reglas declaradas en el schema. Un
  cliente bien formado la evita.

**`invalid_params` no se aplana.** Los errores de validación localization cada campo
con su ruta completa, índices de lista incluidos:

```json
{
  "type": "urn:problem:papersoul:request-validation-failed",
  "status": 422,
  "invalid_params": [
    { "loc": ["body", "pdf_hash"], "msg": "String should match pattern '^[0-9a-f]{64}$'",
      "type": "string_pattern_mismatch" }
  ],
  "trace_id": "f9a03f4e-91bb-447f-946b-f98aa8c3584f"
}
```

Un error en el tercer elemento de una lista reporta `["body", "items", 2, "campo"]`, no
`["body", "items", "campo"]`. Sin el índice, el cliente sabe que un campo falló pero no
cuál, y no puede corregir el payload.

**`type` es la clave, no el status.** El status HTTP puede coincidir entre causas
distintas; el URN no. `title` es estable y **no** se localiza, así que un cliente que
cambia de idioma puede compararlo. `detail` sí se localiza y es diagnóstico: puede
cambiar sin romper a nadie.

`trace_id` es un UUID presente **tanto en la respuesta como en el log del servidor**.
Sin esa correlación, un 5xx es inaccionable desde fuera.

Un 500 nunca incluye el mensaje real de la excepción. `detail` es texto fijo; el
detalle real va al log. Un `RuntimeError("mongodb://user:pass@host:27017")` filtrado
en la respuesta entregaría la topología interna.

### `Content-Type`: dónde se exige y dónde no

| Petición | `Content-Type` ausente | `Content-Type` ≠ JSON |
|---|---|---|
| `POST /documents` **con body** | `415` | `415` |
| `POST /documents` **sin body** | se procesa (fallará por validación) | `415` |
| `GET` / `DELETE` (sin cuerpo) | válido | `415` |

Se acepta `application/json; charset=utf-8` y cualquier `application/*+json`
(RFC 6839). La comparación es por **media type**, nunca por la cadena entera de la
cabecera: comparar la cabecera completa rechazaría peticiones correctas y el síntoma
sería un cliente que «dejó de funcionar» sin tocar el servidor.

---

## Ejecución local

Requisitos: **[uv](https://docs.astral.sh/uv/)** y Docker (para MongoDB).

```bash
git clone https://github.com/LautaroAllolio/PaperSoul-Microservicio-Persistencia.git
cd PaperSoul-Microservicio-Persistencia

uv sync                        # instala dependencias + grupo dev, respeta uv.lock
cp .env.example .env           # plantilla; ajústala si vas a cambiar la URI de Mongo

docker compose up -d mongo     # MongoDB en 127.0.0.1:27017, con healthcheck

uv run uvicorn app.main:app --reload --port 8000
```

Y comprobarlo:

```bash
curl http://localhost:8000/health
# {"status":"ok"}

open http://localhost:8000/docs     # Swagger UI
```

### Con Docker

```bash
docker compose up -d                          # Mongo + mongo-express en :8081
docker build -t papersoul-persistencia .
docker run --rm -p 8000:8000 -e MONGODB_URI=mongodb://host.docker.internal:27017 papersoul-persistencia
```

La imagen es multi-stage, se ejecuta como usuario no-root y el `CMD` es
`uvicorn app.main:app --host 0.0.0.0 --port 8000`. El `--host 0.0.0.0` es
obligatorio: dentro del contenedor `127.0.0.1` no es accesible desde fuera.

`mongo-express` es una GUI para inspeccionar los datos en desarrollo. **No la uses en
ningún entorno compartido**: expone la base de datos sin autenticación.

---

## Calidad y CI

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app   # lint + tipos
uv run pytest -m "not integration"                                       # rápido, SIN Docker
uv run pytest                                                            # todo, requiere Docker
uv run pytest -m "not integration" --cov=app --cov-report=term-missing   # con cobertura
```

El CI (`.github/workflows/ci.yml`) corre en cada push a `main` y en cada PR, con
tres jobs:

| Job | Qué corre | Docker |
|---|---|---|
| `lint-and-unit` | ruff + mypy + suite sin integración, matriz Python **3.11 / 3.12 / 3.13** | no |
| `integration` | G1 contra MongoDB real vía Testcontainers | sí |
| `docker-build` | construye la imagen del `Dockerfile` | sí |

El job `integration` **no** lleva `needs:` respecto al lint a propósito: Mongo no
depende de que el código esté formateado, y encadenarlos haría que un error de estilo
retrasara la única señal que importa ahí, que es si el índice único de Mongo rechaza
el duplicado de verdad.

Las dependencias se instalan con `uv sync --frozen`: si `uv.lock` no cuadra con
`pyproject.toml`, el comando **falla** en vez de re-resolver y escribir un lock
nuevo. Lo que se ejecuta en el CI es exactamente lo que está commiteado.

**Cobertura:** 85 % en `app/` como puerta (`fail_under` en `pyproject.toml`), y
**100 % exigido explícitamente** en `app/exceptions/` y `app/schemas/problem.py`. Esa
segunda puerta es un paso aparte del workflow porque `--cov-fail-under` no puede
expresar «estos dos ficheros al 100 % y el resto al 85 %».

### Tests de integración

Lo que se prueba con Mongo real y no puede probarse con un fake:

- que el **índice único** de Mongo rechaza el `pdf_hash` duplicado de verdad (no sólo
  que la traducción del error da 409);
- round-trip de `uploaded_at` con offset UTC (`tz_aware=True`; sin él el test pasa en
  local y falla en CI);
- orden estable `uploaded_at DESC, _id DESC` en la paginación;
- `DELETE` y lectura posterior.

---

## Configuración

Todo por variable de entorno; plantilla en `.env.example`.

| Variable | Por defecto | Para qué |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | Conexión a MongoDB |
| `MONGODB_DATABASE` | `papersoul` | Nombre de la base |
| `LOG_LEVEL` | `INFO` | Nivel de logging |
| `PROBLEM_TYPE_BASE` | `urn:problem:papersoul` | Prefijo URN de `type` |

`PROBLEM_TYPE_BASE` parece inocuo y no lo es: cambiarlo es un **breaking change** para
todo cliente que haga `switch` sobre `type`. El URN es la clave del contrato de
error, no un detalle de formato.

---

## Desarrollo

```
app/
├── main.py              create_app() + lifespan + montaje de routers
├── api/                 CAPA 1
│   ├── v1/documents.py    los 5 endpoints
│   ├── errors.py          exception handlers → application/problem+json
│   ├── dependencies.py    resolución del contenedor (composition root)
│   ├── openapi_problem.py media type problem+json en el OpenAPI
│   └── health.py
├── services/            CAPA 2
│   └── document_service.py
├── repositories/        CAPA 3
│   ├── base.py            BaseRepository[T]
│   └── pdf_repository.py  contrato + BeaniePdfRepository
├── models/              PdfDocument (Beanie)
├── schemas/             DTOs Pydantic + ProblemDetail + mappers
├── exceptions/          PaperSoulError y la jerarquía de dominio
├── core/                config, container, database, problem_types, logging
└── tests/               unit/ · api/ · integration/
```

Reglas que sostiene el código:

- **El dominio no conoce FastAPI.** `PaperSoulError` no hereda de `HTTPException`:
  importarla en la capa de negocio invertiría la regla de dependencias.
- **Añadir un error no toca el handler.** Se añade una clase en `exceptions/domain.py`
  con `status_code`, `problem_type` y `title`; `api/errors.py` sigue sin ningún `if`
  sobre el tipo de error.
- **El 415 se comprueba antes de Pydantic**, como dependencia. FastAPI lee y parsea
  el body *antes* de invocar la función del endpoint, así que un `raise` dentro de
  ella llega tarde para un body que ni siquiera es JSON.
- **`app/main.py` no entra en la cobertura omitida**: forma parte de `app/` y la
  suite lo monta entero con `create_app`.

### Antes de cada commit

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest -m "not integration"
```

Sin excepciones, ni «ya lo arreglo después».

---

## Seguridad

**El servicio no implementa autenticación ni autorización.** No hay API key, ni
JWT, ni mTLS, ni control de acceso por documento.

Eso significa que cualquiera con acceso de red al puerto 8000 puede:

- leer cualquier documento, incluido su `extracted_text` completo;
- borrar documentos;
- insertar documentos con un `pdf_hash` que bloquee la subida legítima del mismo PDF.

Es una decisión consciente del enunciado (SPEC §1.1, D-3) y **sólo** es aceptable
en red interna. Antes de exponerlo fuera:

1. **MongoDB con credenciales**, vía variable de entorno o secret manager. La plantilla
   actual es `mongodb://localhost:27017` sin usuario ni contraseña.
2. **Autenticación en el borde** (gateway, service mesh, o middleware de FastAPI).
   El servicio no la tiene y no se ha diseñado para añadirla sin revisar los
   endpoints de salud: `/health` debe seguir siendo accesible sin credenciales para
   que el orquestador pueda preguntar.
3. **No publicar mongo-express.** Expone la base de datos sin autenticación.
4. **Mongo ligado a loopback.** El `docker-compose.yml` ya publica `127.0.0.1:27017`
   y no `0.0.0.0`; no lo cambies por comodidad.

Además, y como es habitual en cualquier servicio sin autenticación: los `.env` están en
`.gitignore` y sólo se versiona `.env.example`. Nunca commitees credenciales.

---

## Licencia

Ver [LICENSE](LICENSE).

La especificación completa, con las decisiones de diseño y sus justificaciones, está
en [SPEC.md](SPEC.md).
