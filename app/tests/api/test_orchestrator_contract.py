"""Contrato del Orquestador: POST /api/v1/documents (201).

Congela lo que el Orquestador decodifica en StoredDocumentResponse.
"""

from datetime import UTC, datetime

import httpx

from app.tests.api.conftest import DOCUMENTS_URL, document_payload


async def test_post_201_contains_orchestrator_contract_fields(client: httpx.AsyncClient) -> None:
    """El 201 debe incluir los campos que espera el Orquestador."""
    payload = document_payload(
        filename="contrato-2026.pdf",
        page_count=3,
    )
    response = await client.post(DOCUMENTS_URL, json=payload)

    assert response.status_code == 201
    body = response.json()

    assert body["id"]
    assert body["status"] == "persisted"
    assert body["filename"] == "contrato-2026.pdf"
    assert body["page_count"] == 3
    assert "extracted_text" not in body


async def test_post_201_keeps_additive_fields_for_confirmation(client: httpx.AsyncClient) -> None:
    """pdf_hash y uploaded_at siguen presentes como campos aditivos."""
    payload = document_payload()
    response = await client.post(DOCUMENTS_URL, json=payload)

    assert response.status_code == 201
    body = response.json()

    assert body["pdf_hash"] == payload["pdf_hash"]
    assert "uploaded_at" in body
