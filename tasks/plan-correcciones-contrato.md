# Plan de Correcciones — Contrato con el Orquestador (POST /documents)

> Corrección posterior al build original. El plan SDD vive en [`plan.md`](plan.md) y
> [`todo.md`](todo.md); este documento es un plan nuevo, no los reemplaza.

## Overview

Se detectó un desajuste de contrato entre el **Orquestador** (Go) y este microservicio en
el camino `PROCESSED`: el `POST /api/v1/documents` responde `201` con solo `id`, `status`,
`pdf_hash`, `uploaded_at`, pero el Orquestador decodifica esa respuesta en
`domain.StoredDocumentResponse`, que espera además `filename` y `page_count`.

Como consecuencia, `resultFrom` del Orquestador emite `metadata.fileName: ""` y
`metadata.pageCount: 0`, violando su propio OpenAPI (que exige `fileName` y `pageCount ≥ 1`).
El camino `REUSED` ya quedó alineado en el commit `8da8a86` (endpoint
`GET /by-checksum/{pdf_hash}`), pero el `POST` no.

Este plan alinea el `201` con lo que el Orquestador ya declara y agrega una prueba de
contrato para que el desajuste no vuelva a colarse (su `fakePersistence` de integración lo
enmascaraba).

## Architecture Decisions

- **Fuente de verdad = lo que declara el consumidor.** El Orquestador documenta
  `POST /api/v1/documents → 201 StoredDocumentResponse` (con `filename` y `page_count`).
  Se corrige Persistencia.
- **Cambio aditivo, no rompe consumidores.** Se añaden dos campos al `201`; quien lea
  `id`/`status` sigue funcionando. Precedente: `pdf_hash` y `uploaded_at` ya se añadieron
  como "aditivos" (`app/schemas/document.py:90-95`).
- **SPEC-first.** Regla del repo (`SPEC.md` §7): el cambio de contrato toca `SPEC.md`
  **antes** que el código.
- **Corrección de un solo lado.** No se toca el flujo del Orquestador para no duplicar la
  fuente de verdad del resultado persistido.
- **Guardia de regresión en Persistencia**, que es quien produce la respuesta.

## Integración afectada (referencia)

| Punto | Orquestador consume | Persistencia devuelve hoy | Estado |
|---|---|---|---|
| `GET /api/v1/documents/by-checksum/{h}` | `id, pdf_hash, filename, page_count` | `id, pdf_hash, filename, page_count` | ✅ OK |
| `POST /api/v1/documents` (201) | `id, status, pdf_hash, filename, page_count` | `id, status, pdf_hash, uploaded_at` | ❌ falta `filename`, `page_count` |

Campos que el `StoredDocumentResponse` (Go) decodifica: `id`, `status`, `pdf_hash`,
`filename`, `page_count`. Solo `status` lo ignora el Orquestador (usa su propio valor);
los otros cuatro deben venir.

## Task List

### Phase 1: Alinear el contrato del 201

- [ ] Task 1: Actualizar el contrato en `SPEC.md`
- [ ] Task 2: Añadir `filename` y `page_count` al DTO y al mapper
- [ ] Task 3: Actualizar tests unitarios y de API
- [ ] Task 4: Actualizar el ejemplo del `README.md`

### Checkpoint: Phase 1

- [ ] `uv run ruff check . && uv run ruff format --check . && uv run mypy app` sin errores
- [ ] `uv run pytest -m "not integration"` verde
- [ ] Revisar con el humano antes de la Phase 2

### Phase 2: Guardia de regresión

- [ ] Task 5: Test de contrato que congela los campos que consume el Orquestador

### Checkpoint: Phase 2

- [ ] `uv run pytest tests/api -v` verde
- [ ] El test falla si se quita `filename` o `page_count` del `201`

### Phase 3: Observabilidad (recomendado, no bloqueante)

- [ ] Task 6: Correlación `X-Correlation-Id`

### Checkpoint: Complete

- [ ] El flujo E2E `PROCESSED` devuelve `metadata.fileName` y `metadata.pageCount` válidos
- [ ] Todos los criterios de aceptación cumplidos
- [ ] Listo para revisión

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| El `fakePersistence` del Orquestador sigue enmascarando el drift | Alto | Task 5 congela el contrato en el lado productor |
| `SPEC.md` y código quedan desincronizados | Medio | SPEC-first (Task 1 antes de Task 2), un commit por tarea |
| Docker/Mongo ausentes en desarrollo | Bajo | Estas tareas se validan con fakes y `-m "not integration"` |

## Open Questions

- ¿Endurecimiento defensivo en el Orquestador (fallback a `in.FileName`/`ext.PageCount`)?
  Por defecto: **no** (evita duplicar la fuente de verdad).
- ¿Task 6 (correlación) entra en este ciclo o queda como deuda separada?
  Por defecto: **deuda separada**.
