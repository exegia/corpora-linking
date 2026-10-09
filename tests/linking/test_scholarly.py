import pytest

from corpora_linking import (
    CatalogEntry,
    CitationDiscovery,
    CitationLocator,
    Endpoint,
    Provenance,
    ResolutionResult,
    ScholarlyCitationMention,
    SnapshotCatalog,
    TextLocator,
    TextSnapshot,
    identify_scholarly_citation,
)


def fixture(text="😀 See Smith, Commentary, p. 12."):
    snapshot = TextSnapshot(
        endpoint=Endpoint(
            work_id="essay", edition_id="e", package_id="p", revision="1", document_id="d"
        ),
        stream_id="body",
        text=text,
    )
    start = text.index("Smith")
    exact = "Smith, Commentary, p. 12"
    mention = ScholarlyCitationMention(
        selection=TextLocator(
            stream_id="body",
            start=start,
            end=start + len(exact),
            exact=exact,
            prefix=text[:start],
            suffix=text[start + len(exact) :],
        ),
        cited_name="Smith, Commentary",
        passage=CitationLocator(
            value="p. 12", profile="fixture-print", scheme_id="fixture-pages", scheme_version="1"
        ),
    )
    provenance = Provenance(
        origin="automatic", agent_id="fixture-recognizer", method="explicit-span"
    )
    return snapshot, mention, provenance


@pytest.mark.parametrize(
    "works,status", [((), "unresolved"), (("book",), "resolved"), (("book", "other"), "ambiguous")]
)
def test_identity_is_separate_from_passage_resolution_and_unknowns_survive(works, status):
    snapshot, mention, provenance = fixture()
    catalog = SnapshotCatalog(
        entries=tuple(CatalogEntry(work_id=work, names=(mention.cited_name,)) for work in works)
    )
    discovery = identify_scholarly_citation(snapshot, mention, catalog, provenance=provenance)
    assert discovery.work_identification.status == status
    assert len(discovery.hypotheses) == len(works)
    assert all(
        ref.resolution == "unresolved" and ref.review == "pending" for ref in discovery.hypotheses
    )
    assert discovery.source.locators == (mention.selection,)
    assert snapshot.text[mention.selection.start : mention.selection.end] == mention.selection.exact
    assert CitationDiscovery.model_validate_json(discovery.model_dump_json()) == discovery
    assert all(ref.target.locators == (mention.passage,) for ref in discovery.hypotheses)


@pytest.mark.parametrize("change", ["quote", "context", "stream", "normalization"])
def test_stale_or_incompatible_spans_are_rejected_before_catalog_lookup(change):
    snapshot, mention, provenance = fixture()
    if change == "quote":
        selector = mention.selection.model_copy(
            update={"start": mention.selection.start + 1, "end": mention.selection.end + 1}
        )
    elif change == "context":
        selector = mention.selection.model_copy(update={"prefix": "wrong"})
    elif change == "stream":
        selector = mention.selection.model_copy(update={"stream_id": "other"})
    else:
        snapshot = snapshot.model_copy(update={"text": snapshot.text + "e\u0301"})
        selector = mention.selection.model_copy(update={"normalization": "NFC"})
    mention = mention.model_copy(update={"selection": selector})

    class NoLookup:
        def identify(self, name):
            raise AssertionError("invalid source must not trigger lookup")

    with pytest.raises(ValueError):
        identify_scholarly_citation(snapshot, mention, NoLookup(), provenance=provenance)


def test_catalog_passage_claims_are_not_accepted_as_work_identity():
    snapshot, mention, provenance = fixture()

    class BadCatalog:
        def identify(self, name):
            return ResolutionResult(status="resolved", candidates=(snapshot.endpoint,))

    with pytest.raises(ValueError, match="work-only"):
        identify_scholarly_citation(snapshot, mention, BadCatalog(), provenance=provenance)


def test_unavailable_catalog_and_whole_work_mentions_remain_portable():
    snapshot, mention, provenance = fixture()
    mention = mention.model_copy(update={"passage": None})

    class Unavailable:
        def identify(self, name):
            return ResolutionResult(status="unavailable", diagnostics=("catalog offline",))

    discovery = identify_scholarly_citation(snapshot, mention, Unavailable(), provenance=provenance)
    assert discovery.hypotheses == ()
    assert discovery.work_identification.status == "unavailable"
    assert discovery.mention.cited_name == mention.cited_name
    catalog = SnapshotCatalog(entries=(CatalogEntry(work_id="book", names=(mention.cited_name,)),))
    known = identify_scholarly_citation(snapshot, mention, catalog, provenance=provenance)
    assert known.hypotheses[0].target == Endpoint(work_id="book")
    assert known.hypotheses[0].resolution == "unresolved"


def test_wire_discovery_cannot_self_approve_hypotheses_or_invent_unknown_work():
    snapshot, mention, provenance = fixture()
    catalog = SnapshotCatalog(entries=(CatalogEntry(work_id="book", names=(mention.cited_name,)),))
    discovery = identify_scholarly_citation(snapshot, mention, catalog, provenance=provenance)
    data = discovery.model_dump()
    data["hypotheses"][0]["review"] = "approved"
    with pytest.raises(ValueError, match="pending"):
        CitationDiscovery.model_validate(data)
    data = discovery.model_dump()
    data["work_identification"] = ResolutionResult(status="unresolved").model_dump()
    with pytest.raises(ValueError, match="hypothesis"):
        CitationDiscovery.model_validate(data)


def test_working_scholarly_example_preserves_unknowns_and_verifies_retrieval():
    import runpy
    from pathlib import Path

    example = runpy.run_path(
        str(Path(__file__).resolve().parents[2] / "examples/scholarly_link.py")
    )
    result = example["scholarly_link"]()
    assert result["known"]["work_identification"]["status"] == "resolved"
    assert result["known"]["hypotheses"][0]["resolution"] == "unresolved"
    assert result["retrieval"]["status"] == "resolved"
    assert result["retrieval"]["candidates"][0]["locators"][0]["exact"] == result["retrieved_text"]
    assert result["unknown"]["hypotheses"] == []
    assert len(result["ambiguous"]["hypotheses"]) == 2
    assert (
        CitationDiscovery.model_validate(result["unknown"]).mention.cited_name
        == "Example Author, Notes"
    )
