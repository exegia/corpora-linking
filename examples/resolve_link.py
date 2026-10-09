"""Offline citation detection and verified resolution; no publication side effects."""

from corpora_linking import (
    BibleBook,
    BibleCitationDetector,
    CatalogEntry,
    Endpoint,
    PassageEntry,
    SnapshotCatalog,
    SnapshotResolver,
    TextLocator,
    TextSnapshot,
)


def resolve_link():
    source = Endpoint(
        work_id="essay", edition_id="e", package_id="essay", revision="1", document_id="chapter"
    )
    detector = BibleCitationDetector(
        books=(BibleBook(work_id="john", aliases=("John",)),),
        stream_id="body",
        profile="fixture",
        scheme_id="fixture",
        scheme_version="1",
        agent_id="example",
    )
    (reference,) = detector.detect(source, "Compare John 3:16.")
    base = Endpoint(
        work_id="john",
        edition_id="e",
        package_id="bible",
        revision="sha256:fixture",
        document_id="john",
    )
    text = "Fixture verse text."
    target = Endpoint.model_validate(
        {
            **base.model_dump(),
            "locators": [TextLocator(stream_id="body", start=0, end=len(text), exact=text)],
        }
    )
    resolver = SnapshotResolver(
        catalog=SnapshotCatalog(entries=(CatalogEntry(work_id="john", names=("John",)),)),
        passages=(PassageEntry(citation=reference.target.locators[0], target=target),),
        snapshots=(TextSnapshot(endpoint=base, stream_id="body", text=text),),
    )
    result = resolver.resolve(reference.target)
    assert result.status == "resolved"
    updated = type(reference).model_validate(
        {**reference.model_dump(), "target": result.candidates[0], "resolution": result.status}
    )
    assert updated.id == reference.id and updated.review == "pending"
    return updated


if __name__ == "__main__":
    print(resolve_link().model_dump_json(indent=2))
