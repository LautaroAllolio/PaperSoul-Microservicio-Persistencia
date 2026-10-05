# PaperSoul · Microservicio de Persistencia

Microservicio encargado de **persistir y consultar documentos PDF ya procesados** por
el servicio `Extraer`. Recibe el resultado de la extracción, lo almacena en MongoDB y
responde a consultas por identificador y por resumen criptográfico.

El alcance del servicio es deliberadamente acotado: no extrae texto, no recibe el
binario del PDF y no participa de las decisiones del orquestador. Su responsabilidad
es exclusivamente materializar y recuperar información.

> **Advertencia de seguridad.** Este servicio **no implementa autenticación ni
> autorización**. Cualquier actor con acceso de red al puerto 8000 puede leer y
> eliminar documentos. La sección [Seguridad](#seguridad) detalla esta limitación y los
> requisitos previos a cualquier exposición fuera del entorno de pruebas.

---

## Contenido

1. [Arquitectura](#arquitectura)
2. [Contrato de la API](#contrato-de-la-api)
3. [Modelo de errores: RFC 9457](#modelo-de-errores-rfc-9457)
4. [Ejecución del entorno](#ejecución-del-entorno)
5. [Calidad, pruebas y integración continua](#calidad-pruebas-y-integración-continua)
6. [Configuración](#configuración)
7. [Verificación de la arquitectura](#verificación-de-la-arquitectura)
8. [Seguridad](#seguridad)

---

## Arquitectura

El sistema se organiza en tres capas con dependencias estrictamente unidireccionales.
La regla que las gobierna es que **cada capa conoce únicamente a las que tiene por
debajo y a las compartidas; ninguna tercera capa impone la dirección contraria**.

### 1.1 Capas y responsabilidades

| Capa | Ubicación | Responsabilidad | Conocimiento prohibido |
|---|---|---|---|
| 1 · Presentación | `app/api/` | Transporte HTTP: enrutado, validación de entrada, selección de código de estado y traducción de excepciones a `application/problem+json` | MongoDB, Beanie, reglas de negocio |
| 2 · Negocio | `app/services/` | Casos de uso y orquestación de operaciones | FastAPI, consultas a la base de datos |
| 3 · Datos | `app/repositories/` | Persistencia. Contiene el contrato (`PdfRepository`) y su implementación (`BeaniePdfRepository`) | FastAPI, protocolo HTTP |

### 1.2 Módulos compartidos

Dos paquetes quedan deliberadamente fuera de la estratificación anterior, y su
colocación responde a criterios de acoplamiento:

- **`app/schemas/`** — Objetos de Transferencia de Datos (DTO) que constituyen el
  contrato público hacia el orquestador, además de `ProblemDetail` (RFC 9457), el
  sobre de paginación `Page` y los módulos de mapeo. Su ubicación externa obedece a
  que la capa de negocio requiere tipar sus valores de retorno; de residir dentro de
  `app/api/`, quedaría obligada a importar hacia arriba, con lo que la regla de
  dependencias se vería vulnerada.
- **`app/exceptions/`** — Jerarquía `PaperSoulError`. Son lanzadas por los servicios y
  capturadas por los manejadores HTTP. Al residir en `app/api/`, la capa de negocio
  importaría hacia arriba; en la raíz de `app/`, la relación es simétrica y correcta.

### 1.3 Grafo de dependencias

```
api ──► services ──► repositories ──► models ──► beanie
 │           │                                ▲
 │           └──► schemas ───────────────────-┘
 └──► schemas, exceptions, core
```

Las flechas indican las dependencias **permitidas**; el diagrama no constituye un
inventario exhaustivo de los imports presentes. El grafo es acíclico y su cumplimiento
se verifica mecánicamente; véase [§7](#verificación-de-la-arquitectura).

### 1.4 Inyección de dependencias

`create_app()` construye un `Container` y lo publica en `app.state`. Los routers lo
obtienen mediante `Depends` en el momento de atender cada petición. Ningún router
instancia un repositorio ni un servicio por cuenta propia.

```
create_app()                      app.state.container
     │                                   │
     └── build_container(repo) ──────────┤
                                        ▼
                         get_document_service(request) → DocumentService
```

Esta disciplina permite montar la aplicación completa con un repositorio simulado y sin
base de datos, condición que sostiene la totalidad de las pruebas de API. El
*middleware* de ciclo de vida (`lifespan`) administra exclusivamente el recurso externo,
el cliente de MongoDB; el contenedor no requiere conexión.

---

## Contrato de la API

Prefijo: `/api/v1`. Especificación OpenAPI 3.1 en `/openapi.json`; interfaz Swagger en
`/docs`.

| # | Método | Ruta | Respuesta satisfactoria | Códigos de error |
|---|---|---|---|---|
| 1 | `POST` | `/api/v1/documents` | `201` + cabecera `Location` | 400, 409, 415, 422, 500 |
| 2 | `GET` | `/api/v1/documents` | `200` | 422, 500 |
| 3 | `GET` | `/api/v1/documents/{doc_id}` | `200` | 400, 404, 422, 500 |
| 4 | `GET` | `/api/v1/documents/by-hash/{pdf_hash}` | `200` en todo caso | 400, 415, 422, 500 |
| 5 | `DELETE` | `/api/v1/documents/{doc_id}` | `204` sin cuerpo | 400, 404, 422, 500 |
| — | `GET` | `/health` | `200` | — |

### 2.1 Creación de un documento

```http
POST /api/v1/documents
Content-Type: application/json

{
  "filename": "contrato-2026.pdf",
  "extracted_text": "texto extraído del PDF",
  "extraction_method": "pymupdf",
  "page_count": 3,
  "pdf_hash": "e03726ed…",
  "uploaded_at": null
}
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

El cuerpo de la respuesta omite deliberadamente `extracted_text`, cuyo volumen puede
alcanzar los 10 MB: se trata de un campo que el cliente acaba de remitir. Se incluyen
`pdf_hash` y `uploaded_at` porque permiten confirmar el registro persistido sin
requerir una segunda petición.

**Definición del cuerpo de la solicitud**

| Campo | Tipo | Restricción | Obligatorio |
|---|---|---|---|
| `filename` | `string` | 1 a 255 caracteres | sí |
| `extracted_text` | `string` | 1 a 10 000 000 caracteres | sí |
| `extraction_method` | `"pymupdf"` \| `"ocr"` | enumeración cerrada | sí |
| `page_count` | `integer` | mínimo 1 | sí |
| `pdf_hash` | `string` | `^[0-9a-f]{64}$` | sí |
| `text_hash` | `string` \| `null` | `^[0-9a-f]{64}$` | no |
| `uploaded_at` | `datetime` \| `null` | con desplazamiento UTC | no |

El esquema declara `additionalProperties: false`. Un campo no reconocido produce `422`
en lugar de descartarse en silencio: una propiedad mal escrita indicaría un defecto del
productor, y dicho defecto debe manifestarse de forma explícita en lugar de quedar
almacenado.

`pdf_hash` se valida en minúsculas. No se admite la variante en mayúsculas ni se
normaliza: normalizar ocultaría un defecto del productor precisamente en el campo que
garantiza la deduplicación.

### 2.2 Consulta por identificador

```http
GET /api/v1/documents/665f1c9e8a1b2c3d4e5f6071
```

Devuelve `200` con el documento íntegro, `404` si no existe y `400` si el identificador
no conforms al formato de `ObjectId`.

### 2.3 Consulta por resumen criptográfico

```http
GET /api/v1/documents/by-hash/e03726ed…
```

```json
{ "exists": true,  "id": "665f1c9e8a1b2c3d4e5f6071", "uploaded_at": "2026-10-05T21:10:22.445539Z" }
{ "exists": false, "id": null, "uploaded_at": null }
```

Este recurso responde `200` con independencia de la existencia del documento. La
consulta «¿ha sido ya enviado este PDF?» admite respuesta negativa con la misma
naturalidad que afirmativa; asignar `404` obligaría al consumidor a tratar la ausencia
del recurso como una condición de error.

### 2.4 Listado paginado

```http
GET /api/v1/documents?limit=20&offset=0
```

| Parámetro | Tipo | Límite | Valor por defecto |
|---|---|---|---|
| `limit` | `integer` | 1 a 100 | 20 |
| `offset` | `integer` | ≥ 0 | 0 |

El máximo de `limit` es una decisión deliberada: sin él, una petición del tipo
`?limit=1000000` convertiría el recurso en una extracción íntegra de la colección.

El criterio de ordenación es `uploaded_at` descendente con desempate por `_id`
descendente. El desempate es necesario porque la resolución temporal de BSON es de
milisegundos; sin él, dos documentos registrados en el mismo instante podrían
repetirse o omitirse al paginar.

```json
{ "items": [ … ], "total": 42, "limit": 20, "offset": 0 }
```

### 2.5 Eliminación

```http
DELETE /api/v1/documents/665f1c9e8a1b2c3d4e5f6071
```

Responde `204` sin cuerpo. Una segunda invocación sobre el mismo identificador produce
`404`. La semántica deliberadamente no es idempotente en cuanto al código de estado,
por lo que `404` constituye la respuesta precisa en lugar de un segundo `204`.

### 2.6 Sonda de estado

```http
GET /health
```
```json
{ "status": "ok" }
```

No consulta MongoDB. Una sonda de vitalidad debe verificar que el proceso responde; si
accediera a la base de datos, una degradación del almacén provocaría reinicios del
proceso, con el efecto contrario al perseguido.

---

## Modelo de errores: RFC 9457

Toda respuesta de error se emite como `application/problem+json`, conforme a la RFC
9457. Esta decisión se refleja igualmente en el documento OpenAPI, de modo que un
consumidor pueda analizar el fallo sin conocimiento previo de este servicio.

```json
{
  "type": "urn:problem:papersoul:document-hash-conflict",
  "title": "Conflicto de documento",
  "status": 409,
  "detail": "ya existe un documento con el pdf_hash e03726ed…",
  "instance": "/api/v1/documents",
  "trace_id": "3b8b0a36-cdbb-4f87-b185-e45a015e1db4"
}
```

| Situación | Estado | Identificador `type` |
|---|---|---|
| JSON sintácticamente inválido | `400` | `…:malformed-json` |
| Identificador con formato inválido | `400` | `…:invalid-document-id` |
| `Content-Type` distinto de JSON | `415` | `…:unsupported-media-type` |
| Incumplimiento del esquema | `422` | `…:request-validation-failed` |
| Documento inexistente | `404` | `…:document-not-found` |
| `pdf_hash` duplicado | `409` | `…:document-hash-conflict` |
| Excepción no controlada | `500` | `…:internal-error` |

### 3.1 Criterios de clasificación

La distinción entre `400`, `415` y `422` responde a un criterio semántico, no arbitrario:

- **`400 Bad Request`** — la petición es intraducible o ambigua: un cuerpo que no
  admite interpretación sintáctica, o un identificador cuyo formato hace imposible que
  designe un recurso. Ninguna carga útil válida evita esta condición.
- **`415 Unsupported Media Type`** — la petición resulta comprensible, pero el formato
  de los datos no está admitido por el recurso.
- **`422 Unprocessable Content`** — la petición es comprensible, pero infringe las
  reglas declaradas en el esquema. Una solicitud bien construida la evita.

### 3.2 Miembros de extensión

`invalid_params` conserva la estructura jerárquica de localización de cada
incumplimiento, incluidos los índices de los elementos de listas:

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

Un error en el tercer elemento de una colección se localiza como
`["body", "items", 2, "campo"]`, es decir, con un índice numérico además de la clave.
Sin el índice, el consumidor identificaría el campo fallido pero no su localización
exacta, careciendo de información suficiente para corregir la carga útil.

`trace_id` es un UUID presente **tanto en la respuesta como en el registro de
bitácora** del servidor, lo que permite la correlación entre la incidencia observada y
la traza registrada.

Las respuestas `500` no incorporan el texto de la excepción. `detail` es un literal fijo
y la causa real se consigna únicamente en la bitácora: propagar el texto de la
excepción al consumidor podría filtrar información sobre la topología interna del
despliegue.

### 3.3 Tratamiento de `Content-Type`

| Petición | `Content-Type` ausente | `Content-Type` distinto de JSON |
|---|---|---|
| `POST /documents` **con cuerpo** | `415` | `415` |
| `POST /documents` **sin cuerpo** | se procesa (falla en validación) | `415` |
| `GET` / `DELETE` (sin cuerpo) | válido | `415` |

Se aceptan `application/json` con parámetros (`charset`), así como los tipos
`application/*+json` definidos en la RFC 6839. La comparación se realiza sobre el
*media type* y nunca sobre la cadena íntegra de la cabecera: comparar la cabecera
completa rechazaría peticiones legítimas.

---

## Ejecución del entorno

### 4.1 Requisitos

- [uv](https://docs.astral.sh/uv/)
- Docker, para el servicio de MongoDB

### 4.2 Procedimiento

```bash
git clone https://github.com/LautaroAllolio/PaperSoul-Microservicio-Persistencia.git
cd PaperSoul-Microservicio-Persistencia

uv sync                  # instala dependencias y el grupo dev; respeta uv.lock
cp .env.example .env     # plantilla; ajustar solo si se modifica la URI

docker compose up -d mongo    # MongoDB en 127.0.0.1:27017

uv run uvicorn app.main:app --reload --port 8000
```

Verificación:

```bash
curl http://localhost:8000/health
# {"status":"ok"}
```

La interfaz Swagger se encuentra en `http://localhost:8000/docs`.

### 4.3 Ejecución mediante contenedor

```bash
docker compose up -d     # MongoDB y mongo-express en el puerto 8081

docker build -t papersoul-persistencia .
docker run --rm -p 8000:8000 \
  -e MONGODB_URI=mongodb://host.docker.internal:27017 \
  papersoul-persistencia
```

La imagen se construye en etapas múltiples y se ejecuta como usuario sin privilegios.
El parámetro `--host 0.0.0.0` resulta obligatorio: dentro del contenedor `127.0.0.1` no
es accesible desde el exterior.

---

## Calidad, pruebas y integración continua

### 5.1 Verificación local

```bash
uv run ruff check . && uv run ruff format --check . && uv run mypy app
uv run pytest -m "not integration"                    # sin Docker
uv run pytest                                           # requiere Docker
uv run pytest -m "not integration" --cov=app --cov-report=term-missing
```

### 5.2 Integración continua

El fichero `.github/workflows/ci.yml` define tres trabajos (*jobs*) independientes:

| Trabajo | Contenido | Docker |
|---|---|---|
| `lint-and-unit` | análisis estático, verificación de tipos y batería de pruebas sin integración, sobre las versiones 3.11, 3.12 y 3.13 de Python | no |
| `integration` | pruebas de integración contra MongoDB real mediante Testcontainers | sí |
| `docker-build` | construcción de la imagen a partir del `Dockerfile` | sí |

El trabajo `integration` no declara dependencia respecto del análisis estático. MongoDB
no guarda relación con el estado del análisis del código; encadenarlos retrasaría la
comprobación sustantiva —que el índice único rechace de hecho un duplicado— ante
incidencias de estilo.

Las dependencias se instalan mediante `uv sync --frozen`: si `uv.lock` divergiera de
`pyproject.toml`, el comando fallaría en lugar de resolver las dependencias y reescribir
el fichero de bloqueo versionado.

**Cobertura.** Se exige un 85 % sobre `app/`, umbral definido en `fail_under`
(`pyproject.toml`), y un 100 % en `app/exceptions/` y `app/schemas/problem.py`. Esta
segunda condición se comprueba en un paso adicional del flujo, ya que la opción
`--cov-fail-under` no permite expresar dos umbrales con alcances distintos.

### 5.3 Pruebas de integración

Verifican los aspectos que no pueden comprobarse mediante un doble de prueba:

- el índice único de MongoDB rechaza efectivamente un `pdf_hash` duplicado;
- el recorrido de ida y vuelta de `uploaded_at` con desplazamiento UTC;
- ordenación estable `uploaded_at DESC, _id DESC` en la paginación;
- eliminación y lectura posterior.

---

## Configuración

| Variable de entorno | Valor por defecto | Finalidad |
|---|---|---|
| `MONGODB_URI` | `mongodb://localhost:27017` | Cadena de conexión a MongoDB |
| `MONGODB_DATABASE` | `papersoul` | Nombre de la base de datos |
| `LOG_LEVEL` | `INFO` | Nivel de registro |
| `PROBLEM_TYPE_BASE` | `urn:problem:papersoul` | Prefijo de los URN de `type` |

La modificación de `PROBLEM_TYPE_BASE` constituye un cambio incompatible: los
identificadores `type` constituyen la clave del contrato de error.

---

## Verificación de la arquitectura

La regla de dependencias se comprueba de forma mecánica en
`app/tests/test_architecture.py`, que analiza el árbol de módulos mediante el módulo
`ast` de la biblioteca estándar. Su ejecución no requiere Docker.

Las restricciones verificadas son las siguientes:

| Paquete | Importaciones prohibidas | Fundamento |
|---|---|---|
| `app/api/` | `app.repositories`, `app.models`, `beanie`, `motor`, `pymongo`, `bson` | La capa de presentación alcanza los datos a través de los servicios |
| `app/services/` | `beanie`, `motor`, `pymongo`, `bson` | La capa de negocio se relaciona con la persistencia mediante su contrato |
| `app/schemas/` | `app.services`, `app.api` | Los DTO constituyen el contrato público y no dependen de sus consumidores |
| `app/repositories/` | `app.api`, `app.services` | La capa de datos no conoce las capas superiores |
| `app/exceptions/` | `fastapi`, `app.api`, `app.services`, `app.repositories`, `app.schemas` | El dominio debe permanecer independiente del transporte |
| `app/core/` | `app.api` | El paquete transversal constituye la base, no la cima, de la jerarquía |

La batería incluye, asimismo, la verificación de aciclicidad del grafo de imports
interno, la alcanzabilidad de todo módulo de producción desde `app.main`, y una
comprobación que detecta la eliminación accidental del conjunto de reglas —un conjunto
vacío aprobaría todas las comprobaciones sin verificar nada—.

Las restricciones se aplican a **imports directos**, coherentemente con la
representación de flechas del grafo de dependencias. No se verifica el grafo
transitivo; por ejemplo, la cadena `services → schemas.mappers → models → beanie` existe
de forma intencionada, ya que el módulo de mapeo constituye por definición el puente
entre las fronteras de transporte y persistencia.

---

## Seguridad

**El servicio no implementa autenticación ni autorización.** No se ha definido clave
de API, ni validación de *JSON Web Token*, ni *mutual TLS*, ni control de acceso por
documento.

En consecuencia, cualquier actor con acceso de red al puerto 8000 está en condiciones
de leer documentos —incluido el campo `extracted_text` íntegro—, eliminar registros e
insertar documentos con un `pdf_hash` que bloquee el registro legítimo del mismo
documento.

Esta omisión responde al alcance definido en la especificación del proyecto (SPEC §1.1,
decisión D-3) y resulta admisible únicamente en red interna. Los requisitos previos a
cualquier exposición externa son:

1. **MongoDB con credenciales**, mediante variable de entorno o gestor de secretos. La
   configuración actual carece de usuario y contraseña.
2. **Autenticación en el perímetro** —pasarela, malla de servicios o *middleware* de
   FastAPI—. Debe contemplarse que el recurso `/health` continúe siendo accesible sin
   credenciales, a fin de que el orquestador pueda consultarlo.
3. **No publicar `mongo-express`.** Expone la base de datos sin autenticación.
4. **MongoDB enlazado a la interfaz de loopback.** `docker-compose.yml` publica
   `127.0.0.1:27017` y no `0.0.0.0`; no debe alterarse.

Los ficheros `.env` se encuentran excluidos del control de versiones; únicamente se
versiona `.env.example`. Las credenciales no deben incorporarse al repositorio.

---

## Documentación adicional

- Especificación funcional y decisiones de diseño: [`SPEC.md`](SPEC.md)
- Planificación técnica: `tasks/plan.md`
- Seguimiento de tareas: `tasks/todo.md`
- Licencia: [`LICENSE`](LICENSE)
