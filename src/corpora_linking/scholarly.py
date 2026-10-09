"""Scholarly citation intake: recognition, work identity and passage resolution stay separate."""

from collections.abc import Iterable
from typing import Protocol, Self
from uuid import UUID, uuid4

from pydantic import Field, model_validator

from .interfaces import Catalog
from .locators import normalize_text, verify_text_anchor
from .models import (
    CitationLocator,
    Endpoint,
    NonEmpty,
    Provenance,
    Reference,
    ResolutionResult,
    TextLocator,
    Value,
)
from .resolution import TextSnapshot


class ScholarlyCitationMention(Value):
    """Recognizer-supplied exact span and bibliographic lookup text.

    A passage request is optional and retains the recognizer's explicit scheme.
    A name or page number is never an authority for an exact target selection.
    """

    selection: TextLocator
    cited_name: NonEmpty
    passage: CitationLocator | None = None


class ScholarlyCitationRecognizer(Protocol):
    def recognize(self, snapshot: TextSnapshot) -> Iterable[ScholarlyCitationMention]:
        """Return exact spans; parsers/LLMs must preserve unknown or uncertain names."""
        ...


class CitationDiscovery(Value):
    """Durable intake evidence, including mentions with no identified work.

    Hypotheses are ordinary pending References. Unknown works retain this record
    instead of receiving a fabricated work ID. Persist it before later resolution.
    """

    id: UUID = Field(default_factory=uuid4)
    source: Endpoint
    mention: ScholarlyCitationMention
    provenance: Provenance
    work_identification: ResolutionResult
    hypotheses: tuple[Reference, ...] = ()

    @model_validator(mode="after")
    def consistent(self) -> Self:
        if self.provenance.origin != "automatic":
            raise ValueError("scholarly intake requires automatic provenance")
        if self.source.locators != (self.mention.selection,):
            raise ValueError("discovery source must preserve its exact mention selection")
        works = self.work_identification.candidates
        if any(endpoint != Endpoint(work_id=endpoint.work_id) for endpoint in works):
            raise ValueError("catalog identification must return work-only endpoints")
        if len({endpoint.work_id for endpoint in works}) != len(works):
            raise ValueError("catalog identification contains duplicate works")
        if len(self.hypotheses) != len(works):
            raise ValueError("every identified work requires one retained hypothesis")
        if len({reference.id for reference in self.hypotheses}) != len(self.hypotheses):
            raise ValueError("hypothesis reference IDs must be distinct")
        for work, reference in zip(works, self.hypotheses, strict=True):
            target = Endpoint(
                work_id=work.work_id,
                locators=(self.mention.passage,) if self.mention.passage else (),
            )
            if (
                reference.source != self.source
                or reference.target != target
                or reference.provenance != self.provenance
                or reference.relationship != "core:cites"
                or reference.resolution != "unresolved"
                or reference.review != "pending"
                or reference.publication != "draft"
            ):
                raise ValueError("discovery hypotheses must be matching unresolved pending drafts")
        return self


def identify_scholarly_citation(
    snapshot: TextSnapshot,
    mention: ScholarlyCitationMention,
    catalog: Catalog,
    *,
    provenance: Provenance,
) -> CitationDiscovery:
    """Verify the source and identify works; never invoke a passage resolver.

    Each invocation is a new discovery; integrations must persist/reuse its UUID
    rather than deduplicate citation text or rerun intake as an idempotent write.
    """
    snapshot = TextSnapshot.model_validate(snapshot.model_dump())
    mention = ScholarlyCitationMention.model_validate(mention.model_dump())
    selector = mention.selection
    if selector.stream_id != snapshot.stream_id:
        raise ValueError("citation selection belongs to another stream")
    if normalize_text(snapshot.text, selector.normalization) != snapshot.text:
        raise ValueError("citation selection normalization differs from the pinned stream")
    revision = snapshot.endpoint.revision or ""
    if (
        verify_text_anchor(
            selector, snapshot.text, expected_revision=revision, actual_revision=revision
        )
        != "valid"
    ):
        raise ValueError("stale scholarly citation selection")
    source = Endpoint.model_validate({**snapshot.endpoint.model_dump(), "locators": [selector]})
    works = ResolutionResult.model_validate(catalog.identify(mention.cited_name).model_dump())
    hypotheses = tuple(
        Reference(
            source=source,
            target=Endpoint(
                work_id=work.work_id, locators=(mention.passage,) if mention.passage else ()
            ),
            provenance=provenance,
        )
        for work in works.candidates
    )
    return CitationDiscovery(
        source=source,
        mention=mention,
        provenance=provenance,
        work_identification=works,
        hypotheses=hypotheses,
    )
