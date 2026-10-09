# Contributing

Read README.md, docs/spec.md and AGENTS.md. Keep source formats, storage and UI out
of the core. Use synthetic or openly licensed fixtures, and preserve stable IDs.
Changes to exactness, normalization or resolution need regression coverage for
ambiguity and stale failures as well as successful retrieval. Validate wire inputs;
Pydantic model_copy(update=...) is not a validation boundary.

Run uv sync, uv run ruff check ., uv run mypy, uv run pytest, uv build, and
scripts/verify-wheel.sh. Pull requests should explain the resulting behavior and
validation. Version changes need release notes; never replace an existing artifact
under the same version. Public interfaces are provisional during 0.x; breaking
changes must be documented and reflected in a new minor release.
