import pytest

from corpora_linking import (
    CatalogEntry,
    CitationLocator,
    Endpoint,
    PassageEntry,
    SnapshotCatalog,
    SnapshotResolver,
    TextLocator,
    TextSnapshot,
)


def fixture(revision="1", text="Before. Passage. After."):
    base = Endpoint(
        work_id="john", edition_id="e", package_id="p", revision=revision, document_id="d"
    )
    target = Endpoint.model_validate(
        {
            **base.model_dump(),
            "locators": [
                TextLocator(
                    stream_id="body",
                    start=8,
                    end=16,
                    exact="Passage.",
                    prefix="Before. ",
                    suffix=" After.",
                )
            ],
        }
    )
    return base, target, TextSnapshot(endpoint=base, stream_id="body", text=text)


def citation(**changes):
    return CitationLocator(
        value="John 3:16", profile="fixture", scheme_id="fixture", scheme_version="1", **changes
    )


def catalog():
    return SnapshotCatalog(entries=(CatalogEntry(work_id="john", names=("John",)),))


def resolver(targets, snapshots):
    return SnapshotResolver(
        catalog=catalog(),
        passages=tuple(PassageEntry(citation=citation(), target=t) for t in targets),
        snapshots=snapshots,
    )


def request():
    return Endpoint(work_id="john", locators=(citation(),))


def test_catalog_identity_is_separate_from_passage_resolution():
    c = SnapshotCatalog(
        entries=(
            CatalogEntry(work_id="a", names=("Shared",)),
            CatalogEntry(work_id="b", names=("Shared",)),
        )
    )
    assert c.identify("Shared").status == "ambiguous"
    assert all(not x.locators for x in c.identify("Shared").candidates)
    assert c.identify("shared").status == "unresolved"
    r = SnapshotResolver(catalog=catalog())
    assert r.resolve(Endpoint(work_id="john")).status == "resolved"
    assert r.resolve(request()).status == "unresolved"
    assert r.resolve(Endpoint(work_id="unknown")).status == "unresolved"


def test_verified_mapping_and_manual_selection_share_retrieval():
    _, target, snapshot = fixture()
    r = resolver((target,), (snapshot,))
    assert r.resolve(request()).candidates == (target,)
    assert r.resolve(target).candidates == (target,)
    changed_scheme = Endpoint(
        work_id="john",
        locators=(
            CitationLocator(
                value="John 3:16", profile="fixture", scheme_id="fixture", scheme_version="2"
            ),
        ),
    )
    assert r.resolve(changed_scheme).status == "unresolved"
    assert (
        r.resolve(Endpoint(work_id="john", locators=(citation(reading="variant"),))).status
        == "unresolved"
    )


def test_ambiguous_editions_and_explicit_pinning():
    base, first, s1 = fixture()
    _, second, s2 = fixture("2")
    r = resolver((first, second), (s1, s2))
    assert r.resolve(request()).status == "ambiguous"
    pinned = Endpoint.model_validate({**base.model_dump(), "locators": [citation()]})
    assert r.resolve(pinned).candidates == (first,)
    assert resolver((first, first), (s1,)).resolve(request()).candidates == (first,)


@pytest.mark.parametrize("text", ["Before. Changed. After.", "Wrong!  Passage. After."])
def test_stale_quote_or_context_never_relocated(text):
    _, target, _ = fixture()
    _, _, changed = fixture(text=text)
    result = resolver((target,), (changed,)).resolve(request())
    assert result.status == "unresolved" and not result.candidates
    assert "stale anchor" in result.diagnostics


def test_unavailable_revision_and_incomplete_ambiguity():
    _, first, s1 = fixture()
    _, second, s2 = fixture("2")
    assert resolver((first,), (s2,)).resolve(request()).status == "unavailable"
    result = resolver((first, second), (s1,)).resolve(request())
    assert result.status == "unavailable" and not result.candidates


def test_conflicting_snapshot_content_is_not_chosen():
    _, target, snapshot = fixture()
    _, _, conflict = fixture(text="Before. Changed. After.")
    result = resolver((target,), (snapshot, conflict)).resolve(target)
    assert result.status == "unresolved" and "conflicting pinned snapshots" in result.diagnostics


def test_snapshot_scope_and_unicode_are_validated():
    with pytest.raises(ValueError, match="fully pinned"):
        TextSnapshot(endpoint=Endpoint(work_id="john"), stream_id="body", text="x")
    base, _, _ = fixture()
    with pytest.raises(ValueError, match="surrogates"):
        TextSnapshot(endpoint=base, stream_id="body", text="\ud800")


def test_nfc_stream_is_not_silently_rewritten():
    base, _, _ = fixture()
    target = Endpoint.model_validate(
        {
            **base.model_dump(),
            "locators": [
                TextLocator(stream_id="body", start=0, end=1, exact="é", normalization="NFC")
            ],
        }
    )
    r = resolver((target,), (TextSnapshot(endpoint=base, stream_id="body", text="e\u0301"),))
    assert r.resolve(target).diagnostics == ("stream normalization mismatch",)


def test_resolution_example_preserves_reference_identity_and_review():
    import runpy
    from pathlib import Path

    example = runpy.run_path(str(Path(__file__).resolve().parents[2] / "examples/resolve_link.py"))
    ref = example["resolve_link"]()
    assert ref.resolution == "resolved" and ref.review == "pending"
    assert ref.publication == "draft"
