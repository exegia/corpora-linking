# Agent instructions

Read README.md, docs/spec.md and CONTRIBUTING.md before modifying this package.
Keep the core independent of storage, Supabase, UI, XML, network I/O and heavy parsers.
Preserve opaque IDs, pinned work/edition/package/revision/document scope and exact
stream offsets. Never guess works, pick ambiguous candidates, relocate stale quotes,
or use vector search as exact retrieval authority. Validate wire inputs; unchecked
Pydantic constructors/copies are not validation boundaries.

Run uv run ruff check ., uv run mypy, uv run pytest, uv build and
bash scripts/verify-wheel.sh. Test changes to the contract against the supported
minimum Pydantic version as well as current dependencies. Use synthetic fixtures.
Keep published version/artifacts immutable. Follow docs/releasing.md; no new external
publication, repository changes or deployment without session authorization.
