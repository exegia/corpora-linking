"""Run with uv run python packages/linking/examples/manual_link.py."""

from corpora_linking import Endpoint, Provenance, Reference, TextLocator, verify_text_anchor


def retrieve(endpoint: Endpoint, actual_revision: str, text: str) -> str:
    if len(endpoint.locators) != 1 or not isinstance(endpoint.locators[0], TextLocator):
        raise ValueError("example requires one text selection")
    locator = endpoint.locators[0]
    if (
        verify_text_anchor(
            locator,
            text,
            expected_revision=endpoint.revision or "",
            actual_revision=actual_revision,
        )
        != "valid"
    ):
        raise ValueError("stale anchor")
    return text[locator.start : locator.end]


def manual_link() -> tuple[Reference, str]:
    text = "Before. The linked passage. After."
    target = Endpoint(
        work_id="target",
        edition_id="edition",
        package_id="package",
        revision="1",
        document_id="chapter",
        locators=(
            TextLocator(
                stream_id="body",
                start=8,
                end=27,
                exact="The linked passage.",
                prefix="Before. ",
                suffix=" After.",
            ),
        ),
    )
    source = Endpoint(
        work_id="source",
        edition_id="edition",
        package_id="source-package",
        revision="1",
        document_id="chapter",
        locators=(TextLocator(stream_id="body", start=0, end=17, exact="Selected sentence"),),
    )
    retrieve(source, "1", "Selected sentence")
    passage = retrieve(target, "1", text)
    ref = Reference(
        source=source,
        target=target,
        relationship="core:related",
        provenance=Provenance(origin="manual", agent_id="reader", method="selection"),
        resolution="resolved",
    )
    assert Reference.model_validate_json(ref.model_dump_json()) == ref
    return ref, passage


if __name__ == "__main__":
    reference, passage = manual_link()
    print(reference.id)
    print(passage)
