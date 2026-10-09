# Public core contract

Purpose: share one trustworthy model for reader-created links and citations found
while converting documents, across independently identified works and corpora.
Identifying a work and resolving an exact passage are separate operations.

References allocate opaque UUIDs, record source and target, relationship and creator/
method/evidence provenance, and preserve IDs through serialization. Exact selections
require work, edition, package, immutable revision and document identity. No paragraph
IDs or sentence numbering are mandatory. Missing identity must not be invented.

Resolution is resolved/ambiguous/unresolved/unavailable, independent of pending/
approved/rejected review and draft/published/withdrawn publication. Publishing requires
approved values, but only an external trusted review workflow can authorize approval.
A model value alone is not an audit trail or proof of user authorization.

Text offsets are zero-based half-open Unicode scalar ranges within a named immutable
stream. Unpaired surrogates reject. Exact quote plus optional surrounding context
must match the same offsets in the same revision. Never relocate repeated quotes.
Normalization is preserve by default; NFC must describe an explicitly normalized
stream. Changes to normalization/text require a new revision and explicit mappings.

PDF page numbers start at one; geometry is in unrotated CropBox-relative top-left
points. Rectangles must have positive area, quads must be nondegenerate convex clockwise
polygons. Native text evidence is optional. Format adapters validate asset digest,
page boundaries and transforms; enclosing geometry does not prove exact glyph selection.

EPUB selectors preserve resource href and native opaque CFI; resource-only evidence
has its own type and is not a CFI. HTML selectors preserve parser/version and text-node
paths or multi-node ranges. Adapters translate browser UTF-16 to scalar offsets and
verify DOM/asset identity. The core never parses native files or interprets these values.

Conversion mappings retain original and converted endpoints, method and explicit
exact/approximate/unverified fidelity. An approximate mapping never upgrades itself
into authority for a precise native citation boundary. Changes create new revisions.

Bible detection uses explicit caller registries and numbering context. Alias collisions
retain all hypotheses. Unsupported compound or implicit syntax does not produce guessed
partial references. Scholarly recognizers supply exact source spans and lookup text;
catalog work identity stays distinct from optional passage requests. Unknowns survive
as discovery records, not fabricated work IDs. Detector output starts unresolved/pending.

Catalog/resolver implementations must preserve incomplete coverage and ambiguity.
A missing candidate never justifies selecting the one available edition. Snapshot
adapters trust caller-authoritative identity/text/mappings and perform no remote lookup.
Vectors can help discovery, never exact-selection authority.

The core is independent of database, Supabase, UI, XML, heavyweight parsers and network
I/O. W3C Web Annotation and Readium are design references, not wire-conformance claims.
Corpora's graph and C-USX representations require explicit lossless adapter mappings;
this model supersedes neither. Namespaced transport extensions must be preserved by
those adapters before they claim losslessness. Core wire models reject unknown fields.
