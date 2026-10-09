"""Fixture-backed detection never claims that a passage has been resolved."""

import pytest

from corpora_linking import (
    BibleBook,
    BibleCitationDetector,
    Detector,
    Endpoint,
    Reference,
    TextLocator,
    verify_text_anchor,
)


def detector(*books):
    result: Detector = BibleCitationDetector(
        books=books or (BibleBook(work_id="bible:John", aliases=("John", "Jn.")),),
        stream_id="body",
        profile="fixture:bible",
        scheme_id="fixture:numbering",
        scheme_version="1",
        agent_id="fixture:detector",
    )
    return result


def source():
    return Endpoint(
        work_id="essay",
        edition_id="edition",
        package_id="package",
        revision="sha256:fixture",
        document_id="chapter",
    )


def test_unicode_offsets_original_spelling_and_lifecycle():
    text = "😀 e\u0301 Compare jN. 3:16–18 and John 1:1."
    refs = list(detector().detect(source(), text))
    assert len(refs) == 2
    for ref, quote in zip(refs, ("jN. 3:16–18", "John 1:1"), strict=True):
        anchor = ref.source.locators[0]
        assert isinstance(anchor, TextLocator)
        assert anchor.start == text.index(quote)
        assert anchor.exact == quote
        assert (
            verify_text_anchor(
                anchor, text, expected_revision=source().revision, actual_revision=source().revision
            )
            == "valid"
        )
        assert ref.target.locators[0].value == quote
        assert ref.target.locators[0].scheme_version == "1"
        assert (ref.resolution, ref.review, ref.publication) == ("unresolved", "pending", "draft")
        assert Reference.model_validate_json(ref.model_dump_json()) == ref
    assert refs[0].id != refs[1].id


@pytest.mark.parametrize(
    "text",
    [
        "John 0:1",
        "John 1:0",
        "John 3:18-16",
        "John 3:16-4:2",
        "John 3:16,18",
        "John 3:16; 18",
        "John 3:16-0",
        "John 3:16x",
        "NotJohn 3:16",
        "Unknown 3:16",
        "John 3",
        "3:16",
        "",
    ],
)
def test_unsupported_or_invalid_addresses_are_not_partially_recognized(text):
    assert list(detector().detect(source(), text)) == []


def test_colliding_aliases_preserve_all_unresolved_hypotheses():
    refs = list(
        detector(
            BibleBook(work_id="a", aliases=("John",)), BibleBook(work_id="b", aliases=("John",))
        ).detect(source(), "John 3:16")
    )
    assert {ref.target.work_id for ref in refs} == {"a", "b"}
    assert all(ref.resolution == "unresolved" for ref in refs)
    assert all("ambiguous catalog alias" in ref.provenance.evidence for ref in refs)
    assert refs[0].source == refs[1].source


def test_missing_scope_and_surrogates_rejected_even_without_matches():
    with pytest.raises(ValueError):
        list(detector().detect(Endpoint(work_id="essay"), ""))
    with pytest.raises(ValueError):
        list(detector().detect(source(), "\ud800"))
    selected = Endpoint.model_validate(
        {
            **source().model_dump(),
            "locators": [TextLocator(stream_id="body", start=0, end=1, exact="x")],
        }
    )
    with pytest.raises(ValueError, match="whole stream"):
        list(detector().detect(selected, "John 3:16"))


def test_manual_example_retrieves_and_rejects_stale_selection():
    import runpy
    from pathlib import Path

    example = runpy.run_path(str(Path(__file__).resolve().parents[2] / "examples/manual_link.py"))
    reference, passage = example["manual_link"]()
    assert passage == "The linked passage."
    assert reference.review == "pending"
    for revision, text in (
        ("2", "Before. The linked passage. After."),
        ("1", "Before. A different passage. After."),
    ):
        with pytest.raises(ValueError, match="stale anchor"):
            example["retrieve"](reference.target, revision, text)
