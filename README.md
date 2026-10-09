# Corpora Linking

A small Python library for linking exact passages across documents and corpora.
Manual selections and detected citations use the same reference model. Unknown
works, ambiguous passages and stale anchors stay explicit instead of being guessed.

**Release preparation:** version 0.1.0 is ready for validation but has not been
published. The installation command below becomes available after its PyPI release.

```bash
pip install corpora-linking
```

Requires Python 3.13 or newer and Pydantic 2.11 or newer (below 3). MIT licensed.

## What it does

- Creates opaque UUID reference IDs and preserves them through JSON round trips.
- Records source, target, relationship and automatic/manual provenance.
- Keeps resolution, review and publication as separate states.
- Verifies zero-based half-open Unicode scalar offsets, exact quotes and context.
- Describes scripture, structural, PDF, EPUB CFI and HTML selections as typed values.
- Detects a bounded set of explicit Bible citations with caller-supplied identities.
- Retains scholarly discoveries and all work hypotheses through pluggable recognizers.
- Resolves against explicit caller-authoritative catalog, passage and text snapshots.

The core performs no I/O. A successful work lookup does not prove an exact passage.
Native locator values describe evidence; format-specific adapters must verify it.

## Make and retrieve a manual link

```python
from corpora_linking import (
    CatalogEntry,
    Endpoint,
    Provenance,
    Reference,
    SnapshotCatalog,
    SnapshotResolver,
    TextLocator,
    TextSnapshot,
)

base = Endpoint(
    work_id="example-work",
    edition_id="example-edition",
    package_id="example-package",
    revision="immutable-version-1",
    document_id="chapter-1",
)
snapshot = TextSnapshot(
    endpoint=base,
    stream_id="body",
    text="Before. Linked words. After.",
)
selection = Endpoint.model_validate(
    {
        **base.model_dump(),
        "locators": [
            TextLocator(
                stream_id="body",
                start=8,
                end=20,
                exact="Linked words",
                prefix="Before. ",
                suffix=". After.",
            )
        ],
    }
)
reference = Reference(
    source=selection,
    target=selection,
    provenance=Provenance(origin="manual", agent_id="reader", method="selection"),
)
resolver = SnapshotResolver(
    catalog=SnapshotCatalog(entries=(CatalogEntry(work_id=base.work_id, names=("Example",)),)),
    snapshots=(snapshot,),
)
result = resolver.resolve(reference.target)
assert result.status == "resolved" and result.candidates == (selection,)
locator = selection.locators[0]
print(snapshot.text[locator.start : locator.end])  # Linked words
assert Reference.model_validate_json(reference.model_dump_json()).id == reference.id
```

Both endpoints can point to different snapshots. A whole-work destination is simply
`Endpoint(work_id="another-known-work")`; it contains no fabricated passage.

## Examples

After `pip install .` in this checkout:

```bash
python examples/manual_link.py
python examples/resolve_link.py
python examples/scholarly_link.py
```

The examples use synthetic fixtures, preserve IDs, and exercise verified retrieval,
unknown/ambiguous work identity and citation-to-passage resolution. No credentials,
network service, paragraph IDs or sentence numbering are required.

## Exactness rules

Keep original document identity and immutable version. Offsets count Unicode scalar
values, not UTF-8 bytes or browser UTF-16 code units. Convert browser offsets
explicitly, including emoji. `preserve` is the default normalization policy; NFC
requires an explicitly normalized stored stream and a new revision when text changes.
Quotes and prefix/suffix must match at the original offsets. No quote search relocates
a stale selection. Caller-provided snapshots must be verified against their assets.

PDF page numbers start at one. Geometry uses unrotated CropBox-relative top-left
points; quads are convex clockwise polygons. EPUB CFIs remain native opaque values.
HTML paths refer to a declared parser/version. This package does not parse files,
interpret CFIs, inspect rendered layout, perform OCR, or prove native locator validity.

The Bible detector recognizes explicit supplied-book citations and same-chapter
ranges. It does not infer implicit books, validate verse existence or support every
citation syntax. Catalog identification and exact passage resolution are separate.
Vector similarity can aid discovery; it is never authority for exact retrieval.

## Boundaries

Interfaces allow external catalog, resolver, detector, store and publication adapters.
The core contains no Supabase, database credentials, UI, XML, FastAPI, PyMuPDF,
pdfplumber or EbookLib dependency. Review/publication fields are values, not an
implemented authorization or audit system. Input claiming approval must be checked
by the consuming application's review authority.

The design draws on W3C Web Annotation and Readium Locators; it does not claim wire
conformance to those formats. Corpora's USX/graph contracts remain separate transport
integrations with explicit identity mappings. See [the contract](docs/spec.md).

## Develop and release

```bash
uv sync
uv run ruff check .
uv run mypy
uv run pytest
uv build
```

CI also installs the actual wheel into a fresh environment and executes tests/examples
away from the checkout. See [contributing](CONTRIBUTING.md) and [release setup](docs/releasing.md).
PyPI publication uses GitHub OIDC trusted publishing; no repository token is stored.

**Migration note:** older development `corpora-py` wheels bundle this same Python
namespace. Do not install both distributions together until Corpora switches to the
standalone dependency. The bundled and standalone distributions must have one owner
of `corpora_linking` in any environment.
