import pytest
from pydantic import ValidationError

from corpora_linking import (
    Endpoint,
    EpubLocator,
    HtmlRangeLocator,
    PdfLocator,
    PdfPoint,
    PdfQuad,
    TextLocator,
)


def quad():
    return PdfQuad(
        points=(PdfPoint(x=1, y=1), PdfPoint(x=3, y=1), PdfPoint(x=3, y=2), PdfPoint(x=1, y=2))
    )


@pytest.mark.parametrize(
    "points",
    [
        ((1, 1), (1, 1), (1, 1), (1, 1)),
        ((1, 1), (1, 2), (3, 2), (3, 1)),
        ((1, 1), (3, 2), (3, 1), (1, 2)),
    ],
)
def test_invalid_quad_never_claims_valid_geometry(points):
    with pytest.raises(ValidationError):
        PdfQuad(points=tuple(PdfPoint(x=x, y=y) for x, y in points))


@pytest.mark.parametrize("kind", ["pdf", "epub", "html"])
def test_native_values_roundtrip_with_exact_text_evidence_and_pinned_identity(kind):
    selector = TextLocator(stream_id="native", start=0, end=1, exact="😀")
    if kind == "pdf":
        locator = PdfLocator(asset_id="asset", page=1, quads=(quad(),), text=selector)
    elif kind == "epub":
        locator = EpubLocator(
            asset_id="asset",
            href="chapter.xhtml",
            cfi="epubcfi(/6/2!,/4/1:0,/4/1:2)",
            text=selector,
        )
    else:
        locator = HtmlRangeLocator(
            asset_id="asset",
            parser_version="fixture",
            start_path=(0, 1),
            start_offset=0,
            end_path=(0, 2),
            end_offset=1,
            text=selector,
        )
    endpoint = Endpoint(
        work_id="w",
        edition_id="e",
        package_id="p",
        revision="r",
        document_id="d",
        locators=(locator,),
    )
    assert Endpoint.model_validate_json(endpoint.model_dump_json()) == endpoint
    with pytest.raises(ValidationError):
        Endpoint.model_validate({**endpoint.model_dump(), "revision": None})


def test_pdf_requires_geometry_and_disallows_nonfinite_coordinates():
    with pytest.raises(ValidationError):
        PdfLocator(asset_id="asset", page=1)
    with pytest.raises(ValidationError):
        PdfPoint(x=float("inf"), y=0)
