"""Tests de la jerarquia de excepciones de dominio (lote S2, tarea 2.2).

Objetivo de cobertura: 100% en `app/exceptions/` (SPEC.md §8.5). Es codigo de
contrato puro, sin excusas para dejarlo sin cubrir.
"""

import pytest

from app.core.problem_types import ProblemType
from app.exceptions.base import PaperSoulError
from app.exceptions.domain import (
    DuplicateResourceException,
    InvalidDocumentIdException,
    ResourceNotFoundException,
)

ALL_EXCEPTIONS = [
    ResourceNotFoundException,
    DuplicateResourceException,
    InvalidDocumentIdException,
]


def test_paper_soul_error_defaults_to_internal_server_error() -> None:
    """La raiz asume 500: si una excepcion nueva olvida definir su status, el
    fallo se manifiesta como error interno en vez de como un 4xx silencioso.
    """
    assert PaperSoulError.status_code == 500


def test_paper_soul_error_carries_detail_on_the_instance() -> None:
    """`detail` describe ESTA ocurrencia, a diferencia de status/title/type que son
    del tipo de error. Por eso va en la instancia y no en la clase.
    """
    error = PaperSoulError("detalle concreto")

    assert error.detail == "detalle concreto"


def test_paper_soul_error_is_an_exception() -> None:
    """Debe poder atraparse con `except Exception`, como cualquier otra."""
    with pytest.raises(PaperSoulError):
        raise PaperSoulError("fallo")


@pytest.mark.parametrize("exception_class", ALL_EXCEPTIONS)
def test_domain_exceptions_inherit_from_the_root(
    exception_class: type[PaperSoulError],
) -> None:
    """Esta es la invariante que hace que un solo handler baste para toda la
    jerarquia. Si alguien la rompe, el handler genérico deja de cubrirla y el
    error sale como 500.
    """
    assert issubclass(exception_class, PaperSoulError)


def test_resource_not_found_is_404() -> None:
    """Formato imposible de adivinar es 400; bien formado pero ausente es 404."""
    assert ResourceNotFoundException.status_code == 404
    assert ResourceNotFoundException.problem_type == ProblemType.DOCUMENT_NOT_FOUND
    assert ResourceNotFoundException.title == "Documento no encontrado"


def test_duplicate_resource_is_409() -> None:
    """Conflicto de unicidad: la peticion es valida pero el estado actual la
    rechaza. De ahi el 409 y no el 400.
    """
    assert DuplicateResourceException.status_code == 409
    assert DuplicateResourceException.problem_type == ProblemType.DOCUMENT_HASH_CONFLICT
    assert DuplicateResourceException.title == "Conflicto de documento"


def test_invalid_document_id_is_400() -> None:
    """SC-06: un id que no puede ser un ObjectId es un 400, no un 422 ni un 500.
    Ningun payload valido lo produce, asi que 400 es el codigo correcto.
    """
    assert InvalidDocumentIdException.status_code == 400
    assert InvalidDocumentIdException.problem_type == ProblemType.INVALID_DOCUMENT_ID
    assert InvalidDocumentIdException.title == "Identificador de documento inválido"


@pytest.mark.parametrize("exception_class", ALL_EXCEPTIONS)
def test_domain_exceptions_expose_resource_name(
    exception_class: type[PaperSoulError],
) -> None:
    """`resource_name` es miembro extension de RFC 9457: permite a un cliente
    saber que tipo de recurso fallo sin parsear el `detail`.
    """
    error = exception_class("detalle")

    assert error.resource_name == "document"


@pytest.mark.parametrize("exception_class", ALL_EXCEPTIONS)
def test_status_codes_are_distinct(exception_class: type[PaperSoulError]) -> None:
    """Cada tipo de error mapea a un status distinto; dos clases con el mismo
    status y distinto `type` serian indistinguibles para un cliente que solo mire
    el codigo.
    """
    status_codes = [klass.status_code for klass in ALL_EXCEPTIONS]

    assert len(status_codes) == len(set(status_codes))
    assert exception_class.status_code in status_codes
