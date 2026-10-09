"""Small offline adapters over caller-authoritative catalog and text snapshots."""

from typing import Self

from pydantic import model_validator

from .locators import normalize_text, verify_text_anchor
from .models import (
    CitationLocator,
    Endpoint,
    NonEmpty,
    ResolutionResult,
    ResolutionStatus,
    TextLocator,
    Value,
)


class CatalogEntry(Value):
    work_id: NonEmpty
    names: tuple[NonEmpty, ...]


class SnapshotCatalog(Value):
    """Exact supplied names only; no fuzzy matching or identity inference."""

    entries: tuple[CatalogEntry, ...] = ()

    def identify(self, name: str) -> ResolutionResult:
        candidates = tuple(
            Endpoint(work_id=work)
            for work in sorted({entry.work_id for entry in self.entries if name in entry.names})
        )
        return _result(candidates, "catalog name not found")


class TextSnapshot(Value):
    endpoint: Endpoint
    stream_id: NonEmpty
    text: str

    @model_validator(mode="after")
    def pinned(self) -> Self:
        if self.endpoint.locators or not all(
            getattr(self.endpoint, f) is not None
            for f in ("edition_id", "package_id", "revision", "document_id")
        ):
            raise ValueError("snapshot requires a fully pinned whole-stream endpoint")
        normalize_text(self.text)
        return self


class PassageEntry(Value):
    """A supplied numbering-context mapping, never a generated verse guess."""

    citation: CitationLocator
    target: Endpoint


def _result(candidates: tuple[Endpoint, ...], missing: str) -> ResolutionResult:
    unique = tuple(dict.fromkeys(candidates))
    if not unique:
        return ResolutionResult(status="unresolved", diagnostics=(missing,))
    return ResolutionResult(
        status="resolved" if len(unique) == 1 else "ambiguous", candidates=unique
    )


def _scope_matches(request: Endpoint, target: Endpoint) -> bool:
    return all(
        getattr(request, field) is None or getattr(request, field) == getattr(target, field)
        for field in ("edition_id", "package_id", "revision", "document_id")
    )


class SnapshotResolver(Value):
    """Resolve exact mapping keys, verifying text against pinned snapshots.

    Snapshot providers own checksum verification and catalog authority. Unsupported
    locators, missing streams and stale selections never become verified candidates.
    """

    catalog: SnapshotCatalog
    passages: tuple[PassageEntry, ...] = ()
    snapshots: tuple[TextSnapshot, ...] = ()

    def resolve(self, request: Endpoint) -> ResolutionResult:
        if not any(e.work_id == request.work_id for e in self.catalog.entries):
            return ResolutionResult(status="unresolved", diagnostics=("work not in catalog",))
        if not request.locators:
            if any(
                getattr(request, f) is not None
                for f in ("edition_id", "package_id", "revision", "document_id")
            ):
                return ResolutionResult(
                    status="unresolved", diagnostics=("scoped work needs coverage",)
                )
            return ResolutionResult(status="resolved", candidates=(request,))
        if len(request.locators) != 1:
            return ResolutionResult(
                status="unresolved", diagnostics=("unsupported locator combination",)
            )
        locator = request.locators[0]
        if isinstance(locator, TextLocator):
            return self._verify(request)
        if not isinstance(locator, CitationLocator):
            return ResolutionResult(status="unresolved", diagnostics=("unsupported locator kind",))
        mappings = tuple(
            entry.target
            for entry in self.passages
            if entry.citation == locator
            and entry.target.work_id == request.work_id
            and _scope_matches(request, entry.target)
        )
        if not mappings:
            return ResolutionResult(
                status="unresolved", diagnostics=("citation mapping not found",)
            )
        results = tuple(self._verify(target) for target in mappings)
        # Do not pick a valid candidate when another mapped edition cannot be verified.
        if any(r.status != "resolved" for r in results):
            status: ResolutionStatus = (
                "unavailable" if any(r.status == "unavailable" for r in results) else "unresolved"
            )
            return ResolutionResult(
                status=status,
                diagnostics=tuple(message for r in results for message in r.diagnostics),
            )
        return _result(tuple(c for r in results for c in r.candidates), "no verified passage")

    def _verify(self, target: Endpoint) -> ResolutionResult:
        if len(target.locators) != 1 or not isinstance(target.locators[0], TextLocator):
            return ResolutionResult(
                status="unresolved", diagnostics=("mapping requires a text selection",)
            )
        locator = target.locators[0]
        streams = tuple(
            s
            for s in self.snapshots
            if s.endpoint.work_id == target.work_id
            and _scope_matches(target, s.endpoint)
            and s.stream_id == locator.stream_id
            and not s.endpoint.locators
        )
        if not streams:
            return ResolutionResult(
                status="unavailable", diagnostics=("pinned stream unavailable",)
            )
        if len({s.text for s in streams}) != 1:
            return ResolutionResult(
                status="unresolved", diagnostics=("conflicting pinned snapshots",)
            )
        snapshot = streams[0]
        if target.revision is None or snapshot.endpoint.revision is None:
            return ResolutionResult(status="unresolved", diagnostics=("missing revision",))
        # A stream declared NFC must already be NFC; do not rewrite a pinned asset.
        if normalize_text(snapshot.text, locator.normalization) != snapshot.text:
            return ResolutionResult(
                status="unresolved", diagnostics=("stream normalization mismatch",)
            )
        if (
            verify_text_anchor(
                locator,
                snapshot.text,
                expected_revision=target.revision,
                actual_revision=snapshot.endpoint.revision,
            )
            != "valid"
        ):
            return ResolutionResult(status="unresolved", diagnostics=("stale anchor",))
        return ResolutionResult(status="resolved", candidates=(target,))
