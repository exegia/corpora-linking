"""Ports for future adapters. Implementations own I/O, authorization and history."""

from collections.abc import Iterable
from typing import Protocol
from uuid import UUID

from .models import Endpoint, Reference, ResolutionResult


class Detector(Protocol):
    def detect(self, source: Endpoint, text: str) -> Iterable[Reference]: ...


class Catalog(Protocol):
    def identify(self, name: str) -> ResolutionResult:
        """Identify works only; candidates must not imply passage coverage."""
        ...


class Resolver(Protocol):
    def resolve(self, request: Endpoint) -> ResolutionResult: ...


class ReferenceStore(Protocol):
    def get(self, reference_id: UUID) -> Reference | None: ...

    def save(self, reference: Reference, *, expected_version: int | None) -> int:
        """Compare-and-swap; return new version or raise on conflict."""
        ...


class PublicationAdapter(Protocol):
    def export(self, references: Iterable[Reference]) -> bytes:
        """Preserve IDs; reject unapproved references; do not alter working state."""
        ...

    def import_references(self, payload: bytes) -> Iterable[Reference]:
        """Return records for reconciliation, never automatically approve them."""
        ...
