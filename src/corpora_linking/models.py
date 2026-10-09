"""Transport-neutral linking values; not a replacement for corpus-document v0.4."""

import unicodedata
from typing import Annotated, Literal, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

NonEmpty = Annotated[str, Field(min_length=1)]
ResolutionStatus = Literal["resolved", "ambiguous", "unresolved", "unavailable"]


class Value(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class TextLocator(Value):
    """Zero-based half-open scalar offsets in a named, immutable text stream."""

    kind: Literal["text"] = "text"
    stream_id: NonEmpty
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    exact: NonEmpty
    prefix: str = ""
    suffix: str = ""
    normalization: Literal["preserve", "NFC"] = "preserve"
    offset_unit: Literal["unicode-scalar"] = "unicode-scalar"

    @model_validator(mode="after")
    def span(self) -> Self:
        if self.end <= self.start or len(self.exact) != self.end - self.start:
            raise ValueError("exact quote length must equal the nonempty offset span")
        if any(0xD800 <= ord(c) <= 0xDFFF for c in self.exact + self.prefix + self.suffix):
            raise ValueError("unpaired surrogates are not Unicode scalar values")
        if self.normalization == "NFC" and any(
            unicodedata.normalize("NFC", value) != value
            for value in (self.exact, self.prefix, self.suffix)
        ):
            raise ValueError("quote and context must use the declared NFC normalization")
        return self


class PdfRectangle(Value):
    x0: float = Field(ge=0, allow_inf_nan=False)
    y0: float = Field(ge=0, allow_inf_nan=False)
    x1: float = Field(gt=0, allow_inf_nan=False)
    y1: float = Field(gt=0, allow_inf_nan=False)

    @model_validator(mode="after")
    def area(self) -> Self:
        if self.x1 <= self.x0 or self.y1 <= self.y0:
            raise ValueError("rectangle must have positive area")
        return self


class PdfPoint(Value):
    x: float = Field(ge=0, allow_inf_nan=False)
    y: float = Field(ge=0, allow_inf_nan=False)


class PdfQuad(Value):
    """Clockwise polygon in unrotated CropBox-relative top-left points."""

    points: tuple[PdfPoint, PdfPoint, PdfPoint, PdfPoint]

    @model_validator(mode="after")
    def convex(self) -> Self:
        turns = []
        for i in range(4):
            a, b, c = self.points[i], self.points[(i + 1) % 4], self.points[(i + 2) % 4]
            turns.append((b.x - a.x) * (c.y - b.y) - (b.y - a.y) * (c.x - b.x))
        if not all(turn > 0 for turn in turns):
            raise ValueError("PDF quad must be nondegenerate, convex and clockwise")
        return self


class PdfLocator(Value):
    kind: Literal["pdf"] = "pdf"
    asset_id: NonEmpty
    page: int = Field(ge=1, strict=True)
    rectangles: tuple[PdfRectangle, ...] = ()
    quads: tuple[PdfQuad, ...] = ()
    text: TextLocator | None = None
    coordinates: Literal["unrotated-cropbox-top-left-points"] = "unrotated-cropbox-top-left-points"

    @model_validator(mode="after")
    def geometry(self) -> Self:
        if not self.rectangles and not self.quads:
            raise ValueError("PDF selection requires rectangles or quads")
        return self


class EpubLocator(Value):
    """Native resource path and opaque CFI; this core does not parse EPUB CFI."""

    kind: Literal["epub"] = "epub"
    asset_id: NonEmpty
    href: NonEmpty
    cfi: NonEmpty
    text: TextLocator | None = None


class EpubResourceLocator(Value):
    """An EPUB resource only, without invented fragment or CFI precision."""

    kind: Literal["epub-resource"] = "epub-resource"
    asset_id: NonEmpty
    href: NonEmpty


class HtmlTextLocator(Value):
    """Unicode selection in one text node of a pinned, explicitly parsed DOM."""

    kind: Literal["html-text"] = "html-text"
    asset_id: NonEmpty
    parser: Literal["beautifulsoup-html.parser/v1"] = "beautifulsoup-html.parser/v1"
    parser_version: NonEmpty
    node_path: tuple[Annotated[int, Field(ge=0, strict=True)], ...] = Field(min_length=1)
    text: TextLocator


class HtmlRangeLocator(Value):
    """Range spanning pinned parsed HTML text nodes; Unicode scalar node offsets."""

    kind: Literal["html-range"] = "html-range"
    asset_id: NonEmpty
    parser: Literal["beautifulsoup-html.parser/v1"] = "beautifulsoup-html.parser/v1"
    parser_version: NonEmpty
    start_path: tuple[Annotated[int, Field(ge=0, strict=True)], ...] = Field(min_length=1)
    start_offset: int = Field(ge=0, strict=True)
    end_path: tuple[Annotated[int, Field(ge=0, strict=True)], ...] = Field(min_length=1)
    end_offset: int = Field(ge=0, strict=True)
    text: TextLocator


class StructuralLocator(Value):
    kind: Literal["structural"] = "structural"
    anchor_id: NonEmpty


class CitationLocator(Value):
    """An unresolved address request, never proof of an exact passage."""

    kind: Literal["citation"] = "citation"
    value: NonEmpty
    profile: NonEmpty
    scheme_id: NonEmpty
    scheme_version: NonEmpty
    reading: str | None = None


Locator = Annotated[
    TextLocator
    | PdfLocator
    | EpubLocator
    | EpubResourceLocator
    | HtmlTextLocator
    | HtmlRangeLocator
    | StructuralLocator
    | CitationLocator,
    Field(discriminator="kind"),
]


class Endpoint(Value):
    work_id: NonEmpty
    edition_id: NonEmpty | None = None
    package_id: NonEmpty | None = None
    revision: NonEmpty | None = None
    document_id: NonEmpty | None = None
    locators: tuple[Locator, ...] = ()

    @model_validator(mode="after")
    def scope(self) -> Self:
        physical = any(not isinstance(x, CitationLocator) for x in self.locators)
        scoped = any(x is not None for x in (self.package_id, self.revision, self.document_id))
        if (physical or scoped) and not all(
            x is not None
            for x in (self.edition_id, self.package_id, self.revision, self.document_id)
        ):
            raise ValueError("exact selections require edition/package/revision/document identity")
        return self


class Provenance(Value):
    origin: Literal["automatic", "manual"]
    agent_id: NonEmpty
    method: NonEmpty
    evidence: tuple[str, ...] = ()


class Reference(Value):
    schema_version: Literal["0.1.0"] = "0.1.0"
    id: UUID = Field(default_factory=uuid4)
    source: Endpoint
    target: Endpoint
    relationship: NonEmpty = "core:cites"
    provenance: Provenance
    resolution: ResolutionStatus = "unresolved"
    review: Literal["pending", "approved", "rejected"] = "pending"
    publication: Literal["draft", "published", "withdrawn"] = "draft"

    @model_validator(mode="after")
    def publishable(self) -> Self:
        if self.publication == "published" and self.review != "approved":
            raise ValueError("published references require approval")
        return self


class ConversionMapping(Value):
    """Explicit correspondence, not an identity or equivalence assertion."""

    original: Endpoint
    converted: Endpoint
    method: NonEmpty
    fidelity: Literal["exact", "approximate", "unverified"]


class ResolutionResult(Value):
    status: ResolutionStatus
    candidates: tuple[Endpoint, ...] = ()
    diagnostics: tuple[str, ...] = ()

    @model_validator(mode="after")
    def cardinality(self) -> Self:
        if self.status == "resolved" and len(self.candidates) != 1:
            raise ValueError("resolved requires one verified coverage endpoint")
        if self.status == "ambiguous" and len(self.candidates) < 2:
            raise ValueError("ambiguous requires at least two candidates")
        if self.status in ("unresolved", "unavailable") and self.candidates:
            raise ValueError("failed resolution must not imply a candidate is verified")
        return self
