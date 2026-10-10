# Task List — Correcciones de contrato con el Orquestador (POST /documents)

Plan detallado en [`plan-correcciones-contrato.md`](plan-correcciones-contrato.md).
Un commit por tarea. Todos los comandos se ejecutan desde la raíz de este repositorio.

---

## Task 1: Actualizar el contrato en `SPEC.md`

**Descripción:** Documentar `filename` y `page_count` como campos de la respuesta `201`
antes de tocar el código, respetando la regla "el contrato primero".

**Criterios de aceptación:**
- [ ] `SPEC.md` §5.1 `DocumentPersistedResponse` declara `filename` (`type: string`) y
      `page_count` (`type: integer`, `minimum: 1`).
- [ ] `SPEC.md` §5.2 (ejemplo de `POST /api/v1/documents`) incluye ambos campos.
- [ ] La nota de "campos aditivos" explica que se añaden por el contrato del Orquestador.

**Verificación:**
- [ ] Manual: revisar el diff de `SPEC.md` y su consistencia con §5.2.

**Dependencias:** Ninguna

**Archivos probables:**
- `SPEC.md`

**Scope estimado:** XS (1 archivo)

---

## Task 2: Añadir `filename` y `page_count` al DTO y al mapper

**Descripción:** Incorporar los dos campos a la respuesta del `201` y proyectarlos desde
el documento persistido.

**Criterios de aceptación:**
- [ ] `DocumentPersistedResponse` incluye `filename: str` y
      `page_count: Annotated[int, Field(ge=1)]`.
- [ ] `to_persisted_response` los asigna desde `document.filename` / `document.page_count`.
- [ ] `extracted_text` sigue ausente de la respuesta.

**Verificación:**
- [ ] `uv run mypy app`
- [ ] `uv run ruff check . && uv run ruff format --check .`
- [ ] `uv run pytest tests/unit/test_mappers.py -v`

**Dependencias:** Task 1

**Archivos probables:**
- `app/schemas/document.py`
- `app/schemas/mappers.py`

**Scope estimado:** S (2 archivos)

---

## Task 3: Actualizar tests unitarios y de API

**Descripción:** Reflejar los campos nuevos en los tests existentes que hoy afirman la
forma del `201`.

**Criterios de aceptación:**
- [ ] `tests/unit/test_mappers.py::test_to_persisted_response_returns_only_the_confirmation_fields`
      afirma `filename` y `page_count` (y sigue negando `extracted_text`).
- [ ] `tests/api/test_documents_api.py::test_post_persists_and_answers_201` afirma
      `body["filename"]` y `body["page_count"]`.
- [ ] No se borra ni relaja ninguna aserción existente.

**Verificación:**
- [ ] `uv run pytest tests/unit/test_mappers.py tests/api/test_documents_api.py -v`
- [ ] `uv run pytest -m "not integration"`

**Dependencias:** Task 2

**Archivos probables:**
- `app/tests/unit/test_mappers.py`
- `app/tests/api/test_documents_api.py`

**Scope estimado:** S (2 archivos)

---

## Task 4: Actualizar el ejemplo del `README.md`

**Descripción:** Mantener la documentación técnica alineada con la respuesta real del `201`.

**Criterios de aceptación:**
- [ ] El JSON de ejemplo del `201` incluye `filename` y `page_count`.
- [ ] La prosa sobre campos aditivos los menciona junto a `pdf_hash`/`uploaded_at`.

**Verificación:**
- [ ] Manual: el ejemplo coincide con lo que devuelve la API en Task 3.

**Dependencias:** Task 2

**Archivos probables:**
- `README.md`

**Scope estimado:** XS (1 archivo)

---

## Checkpoint: Phase 1

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy app` sin errores
- [ ] `uv run pytest -m "not integration"` verde
- [ ] Revisar con el humano antes de la Phase 2

---

## Task 5: Test de contrato que congela los campos que consume el Orquestador

**Descripción:** Agregar el test que faltó y que habría detectado el drift: verificar que
el `201` de `POST /documents` contiene los campos que el `StoredDocumentResponse` (Go) del
Orquestador decodifica (`id`, `pdf_hash`, `filename`, `page_count`).

**Criterios de aceptación:**
- [ ] Existe un test de API que afirma `{"id", "pdf_hash", "filename", "page_count"}` como
      subconjunto de las claves del body del `201`.
- [ ] El test documenta que esos campos los consume el Orquestador y que su ausencia produce
      `fileName: ""` / `pageCount: 0` en el `PROCESSED`.
- [ ] Si se quita `filename` o `page_count` del DTO, el test falla.

**Verificación:**
- [ ] `uv run pytest tests/api -v`
- [ ] Prueba negativa manual: quitar un campo en `to_persisted_response`, confirmar el rojo y
      revertir.

**Dependencias:** Task 2

**Archivos probables:**
- `app/tests/api/test_documents_api.py`

**Scope estimado:** S (1 archivo)

---

## Checkpoint: Phase 2

- [ ] `uv run pytest tests/api -v` verde
- [ ] El test falla si se quita `filename` o `page_count` del `201`

---

## Task 6 (OPCIONAL): Correlación `X-Correlation-Id`

**Descripción:** Que el servicio honre y ecóe `X-Correlation-Id` (hoy genera su propio
`trace_id` solo en errores, `app/api/errors.py:32`), para no cortar la cadena de
trazabilidad que propaga el Orquestador. No es requisito de contrato funcional.

**Criterios de aceptación:**
- [ ] Un middleware lee `X-Correlation-Id` (fallback: generar uno) y lo ecóe en la respuesta.
- [ ] El `trace_id` de los `ProblemDetail` usa ese valor cuando llega.
- [ ] Test de API que verifica el eco del header.

**Verificación:**
- [ ] `uv run pytest tests/api -v`
- [ ] `uv run mypy app`

**Dependencias:** Ninguna (independiente de Tasks 1-5)

**Archivos probables:**
- `app/main.py`
- `app/api/errors.py`
- `app/tests/api/` (nuevo test)

**Scope estimado:** M (3-4 archivos)

---

## Checkpoint: Complete

- [ ] El flujo E2E `PROCESSED` devuelve `metadata.fileName` y `metadata.pageCount` válidos
- [ ] Todos los criterios de aceptación cumplidos
- [ ] Listo para revisión

## Backlog / Follow-ups (fuera de este ciclo)

- Endurecimiento defensivo en el Orquestador: fallback a `in.FileName` / `ext.PageCount` si
  `stored` viene vacío.
- Ajustar el `fakePersistence` del test de integración del Orquestador
  (`internal/handler/integration_test.go:798-804`) para que no fabrique campos que el
  downstream real podría no mandar.
- Documentar la coexistencia de `GET /by-hash` (siempre 200) y `GET /by-checksum` (404) como
  dos contratos para dos consumidores.
