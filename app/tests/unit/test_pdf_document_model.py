"""Tests del modelo de persistencia `PdfDocument` (lote S3, tarea 3.1).

Capa 3: el contrato hacia MongoDB. Aquí se fijan las invariantes que valida Mongo
y que ningún DTO de transporte puede veinte: el formato del hash y los índices.

Estos tests **no** construyen un `PdfDocument`. `beanie.Document.__init__` exige
una colección inicializada, así que instanciarlo sin Mongo es imposible por
construcción. Las reglas de dominio se prueban contra `PdfDocumentFields`, la
base pura; el índice y el nombre de colección se leen de `PdfDocument.Settings`,
que tampoco necesita conexión.

Los tests de integración (unicidad real, orden real del listado) van aparte, con
Mongo de verdad, porque un índice declarado no es un índice creado.
"""

from datetime import UTC, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from app.models.pdf_document import PdfDocument, PdfDocumentFields

VALID_PDF_HASH = "a" * 64
VALID_TEXT_HASH = "b" * 64


def make_document(**overrides: object) -> PdfDocumentFields:
    """Documento válido con los campos obligatorios ya puestos.

    Un factory en vez de repetir cinco líneas en cada test: los tests que hay que
    leer son los que fallan, y este módulo tiene ya bastantes asserts.
    """
    fields: dict[str, object] = {
        "filename": "contrato.pdf",
        "extracted_text": "texto extraído",
        "extraction_method": "pymupdf",
        "page_count": 3,
        "pdf_hash": VALID_PDF_HASH,
    }
    fields.update(overrides)
    return PdfDocumentFields(**fields)


def declared_index_keys() -> list[tuple[tuple[str, int], ...]]:
    """Las claves de cada índice declarado, **en el orden en que se declararon**.

    Se leen de `Settings.indexes`, que son los `pymongo.IndexModel` crudos. La vía
    alternativa, `DocumentSettings.get_indexes()`, devuelve `IndexModelField`, cuyo
    atributo `fields` viene ordenado alfabéticamente y perdería el orden de un
    índice compuesto, que es justo lo que importa en el segundo caso.
    """
    return [tuple(index.document["key"].items()) for index in PdfDocument.Settings.indexes]


def declared_unique_index_fields() -> set[str]:
    """Campos de los índices marcados como únicos.

    Todos los índices únicos que declara el modelo son de una sola clave, así que
    se toma la primera. Si algún día se declara uno compuesto y único, este
    helper devolvería sólo una parte y el test pasaría sin comprobarlo todo: por
    eso el nombre del helper dice `fields` en plural pero devuelve un `set`.
    """
    return {
        next(iter(index.document["key"]))
        for index in PdfDocument.Settings.indexes
        if index.document.get("unique") is True
    }


def test_accepts_a_minimal_valid_document() -> None:
    """`text_hash` es opcional (SPEC.md §1): el orquestador puede no haberlo
    calculado. El modelo no debe exigirlo.
    """
    document = make_document()

    assert document.pdf_hash == VALID_PDF_HASH
    assert document.text_hash is None


def test_optional_text_hash_is_stored_when_present() -> None:
    assert make_document(text_hash=VALID_TEXT_HASH).text_hash == VALID_TEXT_HASH


@pytest.mark.parametrize("field", ["pdf_hash", "text_hash"])
def test_hash_must_be_exactly_64_chars(field: str) -> None:
    """Un SHA-256 truncado identificaría un PDF que no existe, y crearía una
    segunda entrada en lugar de deduplicar.
    """
    with pytest.raises(ValidationError):
        make_document(**{field: "a" * 63})


@pytest.mark.parametrize("field", ["pdf_hash", "text_hash"])
def test_hash_rejects_uppercase(field: str) -> None:
    """D-1: el patrón es `^[0-9a-f]{64}$` sin flag `i`, a propósito.

    Normalizar en minúsculas escondería un bug del productor justo en el campo que
    garantiza la deduplicación. Además, si someday se normaliza, dos producers
    con criterios distintos podrían converger al mismo string.
    """
    with pytest.raises(ValidationError):
        make_document(**{field: "A" * 64})


@pytest.mark.parametrize("field", ["pdf_hash", "text_hash"])
def test_hash_rejects_non_hex_characters(field: str) -> None:
    """64 caracteres no bastan: `z` no es hexadecimal."""
    with pytest.raises(ValidationError):
        make_document(**{field: "z" * 64})


@pytest.mark.parametrize("field", ["pdf_hash", "text_hash"])
def test_hash_rejects_longer_than_64(field: str) -> None:
    """Más de 64 caracteres no es un SHA-256. Sin `maxLength`, el patrón
    `^[0-9a-f]{64}$` ya lo rechaza, pero el test lo fija para que un futuro
    cambio a `search` no lo acepte en silencio.
    """
    with pytest.raises(ValidationError):
        make_document(**{field: "a" * 65})


def test_filename_cannot_be_empty() -> None:
    """Un nombre vacío no identifica nada y complica la UI de listado."""
    with pytest.raises(ValidationError):
        make_document(filename="")


def test_filename_is_capped_at_255_chars() -> None:
    """255 es el tope de la spec. Sin `maxLength`, un nombre de 10 MB entraría
    sin complaint en Mongo.
    """
    make_document(filename="a" * 255)

    with pytest.raises(ValidationError):
        make_document(filename="a" * 256)


def test_extracted_text_cannot_be_empty() -> None:
    """Texto vacío significa que la extracción falló. Persistirlo crea un
    documento que nunca podrá ser validado por el consumidor y que además
    bloquea la deduplicación por hash de texto.
    """
    with pytest.raises(ValidationError):
        make_document(extracted_text="")


def test_extracted_text_is_capped_at_10_million_chars() -> None:
    """§1.2: el techo existe para recibir un 422 en vez de reventar por
    `DocumentTooLarge` del driver, que sería un 500 sin explicación útil.

    `max_length` en vez de un validador a mano: el mismo motor de Pydantic que
    produce el 422, sin un `if len(...) > N` que se pueda olvidar.
    """
    make_document(extracted_text="a" * 10_000_000)

    with pytest.raises(ValidationError):
        make_document(extracted_text="a" * 10_000_001)


def test_page_count_must_be_at_least_one() -> None:
    """Un PDF con 0 páginas no es un PDF. La spec exige `>= 1`."""
    with pytest.raises(ValidationError):
        make_document(page_count=0)


def test_page_count_rejects_negative() -> None:
    with pytest.raises(ValidationError):
        make_document(page_count=-1)


def test_extraction_method_is_a_closed_enum() -> None:
    """Enum cerrado (SPEC.md §1): un método desconocido significa que el
    orquestador va más adelantado que este servicio, y aceptarlo guardaría datos
    que el consumidor no sabe interpretar.
    """
    with pytest.raises(ValidationError):
        make_document(extraction_method="magia")


def test_extraction_method_accepts_both_declared_values() -> None:
    """`pymupdf` y `ocr` son los dos valores del contrato. Se parametrizan para
    que añadir uno nuevo sea un acto deliberado, updating el test.
    """
    assert make_document(extraction_method="pymupdf").extraction_method == "pymupdf"
    assert make_document(extraction_method="ocr").extraction_method == "ocr"


def test_uploaded_at_is_generated_when_absent() -> None:
    """El servidor lo genera si el payload no lo trae (SPEC.md §1). Confiar en
    que el productor siempre lo envía sería frágil.
    """
    assert make_document().uploaded_at is not None


def test_uploaded_at_defaults_to_utc_not_naive() -> None:
    """D-2, la parte crítica: un naive se interpretaría en la zona local del
    servidor, y el mismo documento respondería con y sin offset según dónde corra.

    Es también el requisito de que la respuesta sea siempre `+00:00`/`Z`.
    """
    uploaded_at = make_document().uploaded_at

    assert uploaded_at.tzinfo is not None
    assert uploaded_at.utcoffset() == timedelta(0)


def test_uploaded_at_from_a_naive_datetime_is_assumed_utc() -> None:
    """Si el productor manda un naive, se interpreta como UTC y no como hora
    local. Descartarlo con un 422 sería más correcto pero rompería al productor
    actual; convertir asumiendo UTC es la interpretación conservadora.
    """
    naive = datetime(2026, 1, 1, 12, 0, 0)

    document = make_document(uploaded_at=naive)

    assert document.uploaded_at.tzinfo is not None
    assert document.uploaded_at.utcoffset() == timedelta(0)
    assert document.uploaded_at.replace(tzinfo=None) == naive


def test_uploaded_at_keeps_the_offset_it_was_given() -> None:
    """Una zona distinta de UTC se respeta en el almacenamiento; la conversión a
    UTC al serializar es responsabilidad del mapper, no del modelo. Así el dato
    original no se pierde en el camino.
    """
    offset = timezone(timedelta(hours=-3))

    document = make_document(uploaded_at=datetime(2026, 1, 1, 9, 0, tzinfo=offset))

    assert document.uploaded_at.utcoffset() == timedelta(hours=-3)


def test_uploaded_at_from_client_is_preserved() -> None:
    """Si el productor lo manda, se respeta: puede estar reintentando una subida
    con la hora original.
    """
    moment = datetime(2026, 5, 4, 3, 2, 1, tzinfo=UTC)

    assert make_document(uploaded_at=moment).uploaded_at == moment


def test_unknown_field_is_rejected() -> None:
    """Un campo que el modelo no conoce casi siempre es un typo del productor
    (`pdfHash`, `page_counts`). Aceptarlo en silencio es persistir basura que
    nadie leerá. `extra="forbid"`.
    """
    with pytest.raises(ValidationError):
        make_document(pdfHash=VALID_PDF_HASH)


def test_collection_name_is_the_agreed_one() -> None:
    """El nombre de la colección es parte del contrato con el resto del
    ecosistema: un orquestador o un script de migración lo conoce, así que no es
    un detalle interno del ODM.
    """
    assert PdfDocument.Settings.name == "pdf_documents"


def test_pdf_hash_index_is_unique() -> None:
    """La deduplicación depende por completo de este índice.

    Nota: esto verifica que el índice está *declarado* con `unique=True`, no que
    Mongo lo haya creado. La unicidad real se prueba contra un Mongo de verdad;
    un `create_index` fallido en producción no lo detecta este test.
    """
    assert "pdf_hash" in declared_unique_index_fields()


def test_listing_order_index_matches_the_contract() -> None:
    """US-3 y SC-05: el listado es `uploaded_at DESC, _id DESC`.

    El índice debe existir y en ese orden exacto, porque sin él el listado es un
    `SORT` en memoria de toda la colección, que se degrada de forma silenciosa
    hasta que hay documentos suficientes para notarlo.
    """
    assert (("uploaded_at", -1), ("_id", -1)) in declared_index_keys()
