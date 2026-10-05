"""Tests del modelo ProblemDetail de RFC 9457 (lote S2, tarea 2.1)."""

from app.core.problem_types import ProblemType
from app.schemas.problem import PROBLEM_JSON, InvalidParam, ProblemDetail


def test_required_members_are_type_title_status() -> None:
    """RFC 9457 marca type, title y status como los unicos miembros obligatorios."""
    detail = ProblemDetail(type=ProblemType.DOCUMENT_NOT_FOUND, title="No encontrado", status=404)

    assert detail.type == ProblemType.DOCUMENT_NOT_FOUND
    assert detail.title == "No encontrado"
    assert detail.status == 404


def test_type_defaults_to_about_blank() -> None:
    """`about:blank` es el valor por defecto de la RFC: absence de informacion
    sobre el problema, no "tipo generico".
    """
    assert ProblemDetail(title="Algo", status=500).type == "about:blank"


def test_problem_json_media_type_is_the_rfc_one() -> None:
    """La constante centraliza el media type: repetir el literal en varios sitios
    es como se acaba sirviendo application/json por error en un endpoint.
    """
    assert PROBLEM_JSON == "application/problem+json"


def test_dump_excludes_none_members() -> None:
    """Los miembros ausentes no se serializan.

    RFC 9457 dice que un miembro ausente significa "sin informacion", y un
    `detail: null` es distinto: un cliente que hace `if "detail" in body` no lo
    trataria igual. Se omiten, no se anaden como null.
    """
    payload = ProblemDetail(title="No encontrado", status=404).model_dump(exclude_none=True)

    assert set(payload) == {"type", "title", "status"}


def test_extension_members_are_allowed() -> None:
    """RFC 9457 permite miembros extension. `extra="allow"` los deja pasar en
    lugar de recortarlos en silencio.
    """
    detail = ProblemDetail(
        title="No encontrado",
        status=404,
        resource_name="document",
        retry_after=30,
    )

    assert detail.resource_name == "document"
    assert detail.retry_after == 30


def test_invalid_params_loc_preserves_list_index() -> None:
    """SC-10: `loc` NO se aplana.

    Un error en el tercer elemento de una lista debe reportar el indice. Sin el,
    el cliente sabe que un campo fallo pero no *cual* elemento, y no puede
    corregir el payload.
    """
    param = InvalidParam(loc=["body", "items", 2, "extracted_text"], msg="es obligatorio")

    assert param.loc == ["body", "items", 2, "extracted_text"]


def test_invalid_params_loc_accepts_strings_and_ints() -> None:
    """`loc` mezcla nombres de campo (str) e indices (int). Un tipo unico obligaria
    a convertir los indices a texto y perderia el orden de la ruta.
    """
    detail = ProblemDetail(
        title="No procesable",
        status=422,
        invalid_params=[
            InvalidParam(loc=["body", "filename"], msg="demasiado largo", type="string_too_long"),
            InvalidParam(loc=["body", "items", 0], msg="no encontrado", type="missing"),
        ],
    )

    params = detail.invalid_params or []
    assert len(params) == 2
    first, second = params
    assert first.type == "string_too_long"
    assert second.loc[-1] == 0


def test_invalid_params_type_is_optional_because_pydantic_may_omit_it() -> None:
    """Pydantic no siempre emite `type` en sus errores (p.ej. errores de union).
    El campo es opcional para no perder el resto del error por eso.
    """
    param = InvalidParam(loc=["body"], msg="campo ausente")

    assert param.type is None


def test_serializes_to_a_json_object() -> None:
    """El modelo debe ser serializable como dict JSON con los miembros que RFC 9457
    define, sin envolturas adicionales.
    """
    detail = ProblemDetail(
        type=ProblemType.REQUEST_VALIDATION_FAILED,
        title="Solicitud no procesable",
        status=422,
        detail="page_count debe ser mayor o igual a 1",
        instance="/api/v1/documents",
        invalid_params=[
            InvalidParam(
                loc=["body", "page_count"],
                msg="input should be >= 1",
                type="greater_than_equal",
            )
        ],
    )

    payload = detail.model_dump(exclude_none=True, mode="json")

    assert payload["type"] == "urn:problem:papersoul:request-validation-failed"
    assert payload["status"] == 422
    assert payload["instance"] == "/api/v1/documents"
    assert payload["invalid_params"][0]["loc"] == ["body", "page_count"]
