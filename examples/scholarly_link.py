"""Synthetic scholarly citation intake followed by separate exact retrieval."""

import json

from corpora_linking import (
    CatalogEntry,
    CitationLocator,
    Endpoint,
    PassageEntry,
    Provenance,
    ScholarlyCitationMention,
    SnapshotCatalog,
    SnapshotResolver,
    TextLocator,
    TextSnapshot,
    identify_scholarly_citation,
)


def scholarly_link() -> dict:
    text = "😀 See Example Author, Notes, p. 12."
    quote = "Example Author, Notes, p. 12"
    start = text.index(quote)
    source = TextSnapshot(
        endpoint=Endpoint(
            work_id="fixture:essay",
            edition_id="fixture",
            package_id="source",
            revision="1",
            document_id="body",
        ),
        stream_id="body",
        text=text,
    )
    passage = CitationLocator(
        value="p. 12", profile="fixture-print", scheme_id="fixture-pages", scheme_version="1"
    )
    mention = ScholarlyCitationMention(
        selection=TextLocator(
            stream_id="body",
            start=start,
            end=start + len(quote),
            exact=quote,
            prefix=text[:start],
            suffix=".",
        ),
        cited_name="Example Author, Notes",
        passage=passage,
    )
    provenance = Provenance(
        origin="automatic", agent_id="fixture-recognizer", method="explicit-span"
    )
    catalog = SnapshotCatalog(
        entries=(CatalogEntry(work_id="fixture:notes", names=(mention.cited_name,)),)
    )
    discovery = identify_scholarly_citation(source, mention, catalog, provenance=provenance)
    reference = discovery.hypotheses[0]
    target = TextSnapshot(
        endpoint=Endpoint(
            work_id="fixture:notes",
            edition_id="fixture",
            package_id="target",
            revision="1",
            document_id="page-12",
        ),
        stream_id="body",
        text="A synthetic passage for the retrieval example.",
    )
    selected = Endpoint.model_validate(
        {
            **target.endpoint.model_dump(),
            "locators": [
                TextLocator(stream_id="body", start=0, end=len(target.text), exact=target.text)
            ],
        }
    )
    resolver = SnapshotResolver(
        catalog=catalog,
        passages=(PassageEntry(citation=passage, target=selected),),
        snapshots=(target,),
    )
    resolution = resolver.resolve(reference.target)
    assert resolution.status == "resolved" and resolution.candidates == (selected,)
    unknown = identify_scholarly_citation(source, mention, SnapshotCatalog(), provenance=provenance)
    ambiguous = identify_scholarly_citation(
        source,
        mention,
        SnapshotCatalog(
            entries=(
                CatalogEntry(work_id="fixture:notes", names=(mention.cited_name,)),
                CatalogEntry(work_id="fixture:other-notes", names=(mention.cited_name,)),
            )
        ),
        provenance=provenance,
    )
    return {
        "known": discovery.model_dump(mode="json"),
        "retrieval": resolution.model_dump(mode="json"),
        "retrieved_text": target.text,
        "unknown": unknown.model_dump(mode="json"),
        "ambiguous": ambiguous.model_dump(mode="json"),
    }


if __name__ == "__main__":
    print(json.dumps(scholarly_link(), ensure_ascii=False, indent=2))
