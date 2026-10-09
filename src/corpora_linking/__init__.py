"""Public linking core, independent of Corpora, storage and source parsers."""

from .detection import BibleBook, BibleCitationDetector
from .interfaces import Catalog, Detector, PublicationAdapter, ReferenceStore, Resolver
from .locators import normalize_text, verify_text_anchor
from .models import (
    CitationLocator,
    ConversionMapping,
    Endpoint,
    EpubLocator,
    EpubResourceLocator,
    HtmlRangeLocator,
    HtmlTextLocator,
    Locator,
    PdfLocator,
    PdfPoint,
    PdfQuad,
    PdfRectangle,
    Provenance,
    Reference,
    ResolutionResult,
    StructuralLocator,
    TextLocator,
)

__all__ = [
    "CitationDiscovery",
    "ScholarlyCitationMention",
    "ScholarlyCitationRecognizer",
    "identify_scholarly_citation",
    "Catalog",
    "CatalogEntry",
    "PassageEntry",
    "SnapshotCatalog",
    "SnapshotResolver",
    "TextSnapshot",
    "BibleBook",
    "BibleCitationDetector",
    "CitationLocator",
    "ConversionMapping",
    "Detector",
    "Endpoint",
    "EpubLocator",
    "EpubResourceLocator",
    "HtmlTextLocator",
    "HtmlRangeLocator",
    "PdfPoint",
    "PdfQuad",
    "Locator",
    "PdfLocator",
    "PdfRectangle",
    "Provenance",
    "PublicationAdapter",
    "Reference",
    "ReferenceStore",
    "ResolutionResult",
    "Resolver",
    "StructuralLocator",
    "TextLocator",
    "normalize_text",
    "verify_text_anchor",
]

from .resolution import CatalogEntry, PassageEntry, SnapshotCatalog, SnapshotResolver, TextSnapshot
from .scholarly import (
    CitationDiscovery,
    ScholarlyCitationMention,
    ScholarlyCitationRecognizer,
    identify_scholarly_citation,
)
