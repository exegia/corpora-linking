import pytest
from pydantic import ValidationError

from corpora_linking import (
    CitationLocator,
    ConversionMapping,
    Endpoint,
    EpubLocator,
    PdfLocator,
    PdfRectangle,
    Provenance,
    Reference,
    ResolutionResult,
    StructuralLocator,
    TextLocator,
    normalize_text,
    verify_text_anchor,
)


def endpoint(*locators):
    return Endpoint(
        work_id="work",
        edition_id="edition",
        package_id="package",
        revision="r1",
        document_id="doc",
        locators=locators,
    )


def test_manual_and_automatic_roundtrip_without_paragraph_ids():
    for origin in ("manual", "automatic"):
        source = endpoint(TextLocator(stream_id="body", start=1, end=4, exact="😀é"))
        reference = Reference(
            source=source,
            target=Endpoint(work_id="commentary"),
            provenance=Provenance(origin=origin, agent_id="agent", method="selection"),
        )
        restored = Reference.model_validate_json(reference.model_dump_json())
        assert restored == reference
        assert restored.id == reference.id
        assert restored.target.locators == ()
        assert restored.resolution == "unresolved"


@pytest.mark.parametrize(
    "locator",
    [
        PdfLocator(asset_id="pdf", page=2, rectangles=(PdfRectangle(x0=1, y0=2, x1=3, y1=4),)),
        EpubLocator(asset_id="epub", href="chapter.xhtml", cfi="epubcfi(/6/2!/4/2)"),
        StructuralLocator(anchor_id="anchor"),
        CitationLocator(value="MAT 3:1", profile="bible", scheme_id="scheme", scheme_version="1"),
    ],
)
def test_typed_locators_and_conversion_mapping_roundtrip(locator):
    mapping = ConversionMapping(
        original=endpoint(locator),
        converted=endpoint(StructuralLocator(anchor_id="span")),
        method="fixture",
        fidelity="unverified",
    )
    assert ConversionMapping.model_validate_json(mapping.model_dump_json()) == mapping


@pytest.mark.parametrize(
    "changes",
    [
        {"start": -1},
        {"end": 0},
        {"end": 2},
        {"start": True},
        {"exact": "\ud800"},
        {"offset_unit": "utf16"},
        {"normalization": "NFKC"},
    ],
)
def test_invalid_text_locators(changes):
    with pytest.raises(ValidationError):
        TextLocator.model_validate(
            {"stream_id": "body", "start": 0, "end": 1, "exact": "x", **changes}
        )


def test_missing_scope_and_unknown_fields_rejected():
    with pytest.raises(ValidationError):
        Endpoint(work_id="work", locators=(StructuralLocator(anchor_id="x"),))
    with pytest.raises(ValidationError):
        Endpoint.model_validate({"work_id": "work", "paragraph": "invented"})


@pytest.mark.parametrize(
    "rectangle",
    [
        {"x0": 3, "y0": 0, "x1": 2, "y1": 1},
        {"x0": 0, "y0": 0, "x1": float("inf"), "y1": 1},
    ],
)
def test_invalid_pdf_geometry(rectangle):
    with pytest.raises(ValidationError):
        PdfRectangle.model_validate(rectangle)


def test_unicode_normalization_and_stale_context():
    text = "A😀é\n"
    locator = TextLocator(stream_id="body", start=1, end=4, exact="😀é", prefix="A", suffix="\n")
    assert len(normalize_text(text)) == 5
    assert len(normalize_text(text, "NFC")) == 4
    assert verify_text_anchor(locator, text, expected_revision="1", actual_revision="1") == "valid"
    for changed, revision in ((text, "2"), ("B😀é\n", "1"), ("A😀é!", "1"), ("A😀é\n", "1")):
        assert (
            verify_text_anchor(locator, changed, expected_revision="1", actual_revision=revision)
            == "stale"
        )
    with pytest.raises(ValueError):
        normalize_text("\ud800")


def test_repeated_quote_does_not_relocate():
    locator = TextLocator(stream_id="body", start=0, end=3, exact="foo", suffix=" bar")
    assert (
        verify_text_anchor(locator, "foo baz foo bar", expected_revision="1", actual_revision="1")
        == "stale"
    )


def test_resolution_and_review_are_independent():
    reference = Reference(
        source=endpoint(),
        target=Endpoint(work_id="work"),
        resolution="ambiguous",
        provenance=Provenance(origin="manual", agent_id="reader", method="selection"),
        review="approved",
    )
    assert reference.resolution == "ambiguous"
    with pytest.raises(ValidationError):
        Reference.model_validate(
            {**reference.model_dump(), "review": "pending", "publication": "published"}
        )
    with pytest.raises(ValidationError):
        ResolutionResult(status="resolved")
    with pytest.raises(ValidationError):
        ResolutionResult(status="ambiguous", candidates=(endpoint(),))
    assert ResolutionResult(status="resolved", candidates=(Endpoint(work_id="work"),)).candidates
    assert (
        ResolutionResult(status="ambiguous", candidates=(endpoint(), endpoint())).status
        == "ambiguous"
    )
    for status in ("unresolved", "unavailable"):
        assert ResolutionResult(status=status, diagnostics=("missing catalog",)).candidates == ()


def test_nfc_quotes_must_match_declared_stream_policy():
    with pytest.raises(ValidationError, match="declared NFC"):
        TextLocator(stream_id="body", start=0, end=2, exact="é", normalization="NFC")
    locator = TextLocator(stream_id="body", start=0, end=1, exact="é", normalization="NFC")
    assert verify_text_anchor(locator, "é", expected_revision="1", actual_revision="1") == "valid"


def test_resolver_port_supports_work_only_and_unavailable_results():
    from corpora_linking import Resolver

    class CatalogResolver:
        def resolve(self, request: Endpoint) -> ResolutionResult:
            if request.work_id == "known-work":
                return ResolutionResult(status="resolved", candidates=(request,))
            return ResolutionResult(status="unavailable", diagnostics=("catalog offline",))

    resolver: Resolver = CatalogResolver()
    resolved = resolver.resolve(Endpoint(work_id="known-work"))
    assert resolved.candidates[0].locators == ()
    assert resolver.resolve(Endpoint(work_id="unknown")).status == "unavailable"
