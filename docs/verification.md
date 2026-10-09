# Initial release verification

Version 0.1.0 is a prepared candidate, not a completed PyPI release.

- 65 core tests pass on Python 3.13 with Pydantic 2.14.0.
- All 65 tests pass against the actual wheel with minimum Pydantic 2.11.0.
- All 65 tests pass on Python 3.14.7 with the current Pydantic version.
- Ruff and mypy pass. Core examples are included in type checking.
- Wheel and sdist build independently with no Corpora workspace dependency.
- The actual wheel installs in a fresh environment; all tests and all three examples
  run away from the source checkout. Runtime dependencies are Pydantic and its own
  dependencies; pytest is installed solely to run checks.
- Workflow YAML parses, actions use pinned commit hashes, tag/version matching is
  enforced, and only the publishing job requests an OIDC token. GitHub Actions CI passed for the initial feature commit
  d126ae22e32e6187174021ff94cfabbfed7db510 (run 37877737824).
  Real publishing authentication remains unverified.
- The GitHub repository is accessible and contains an initial Homebrew CLI copy.
  The feature branch replaces that copy with the independently verified core while
  preserving the remote history. Repository visibility is now public.
- PyPI availability and publisher authentication still require release-time checks.

The source excludes the monorepo-specific TF adapter test while preserving core
model assertions. Additional native-value tests cover pinned identity, PDF convexity,
nonfinite coordinates and native quote-selector serialization. There are no native
parser, Supabase, or UI dependencies in this release. Heavy format behavior belongs
to integration tests outside this repository.

The owner created the GitHub repository and reports publishing setup ready.
The public visibility change is verified. The owner marked PR #2 ready and
merged it into dev. Promotion PR #3 passed CI (run 37880606921); its merge and a reviewed main release remain
before tagging and publishing.
No PyPI publication or installed-from-PyPI verification has yet completed.
See releasing.md for the publication procedure; use Actions trusted publishing.
