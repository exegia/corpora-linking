# Branching and release

Feature branches use `<type>/<slug>`, start from `dev`, and open pull requests
back into `dev`. Preserve the existing `dev`, `next`, and `main` history.
Promote reviewed work from `dev` through `next` to `main` with pull requests.
No automatic promotion or merge is enabled by this package.

This repository contains the independent `corpora-linking` Python package.
The initial repository was copied from the Homebrew CLI; its formula, CLI,
and Homebrew release automation do not apply and are removed. The CLI and
tap remain in `exegia/homebrew-corpora`.

CI validates Python 3.13 and 3.14 and the minimum supported Pydantic version.
Publishing uses `.github/workflows/publish.yml`, the `pypi` environment, and
PyPI trusted publishing. See `docs/releasing.md` for the release procedure.
Create release tags only from reviewed, passing package commits. Existing
GitHub rulesets may require the organization automation App to create tags
or merge release branches; do not bypass protections or force-push.
