# Release verification

Version 0.1.0 is published on PyPI. The immutable v0.1.0 tag points to
496a5caaf84a61b994b2bc4dacd089568e7f0c54. Both verify and publish jobs passed
in GitHub Actions run 37881455925 using PyPI trusted publishing.

Both wheel and source archive are present on PyPI. A fresh Python 3.13
environment installed corpora-linking==0.1.0 from PyPI with the package cache
disabled. All 65 portable tests and the three examples passed away from the
checkout; the import path was inside that environment's site-packages.

Local checks also passed on Python 3.14.7 and against minimum Pydantic
2.11.0. Ruff and mypy passed, and wheel/sdist installation was verified.

PyPI artifact SHA-256:

- Wheel: 3c4af8ab00ab37176037371411b9ddf46b26b95a7ba148b697bf1a9945f8a98c
- Source: 5c73905c5692997e40891e151a908fa02d8c6194b2b97ea8f3e1e53fe3d9fa6a

The public repository preserves the initially unrelated dev, next and main
histories through reviewed promotion/release PRs #2, #3 and #4. No branches
were reset or force-pushed. No database or service deployment was performed.

Native format parsing, persistence, authentication, and C-USX integration stay
outside this core. Corpora and Homebrew dependency migration follows this
verified publication. Release artifacts remain immutable; documentation updates
do not rebuild or replace version 0.1.0.
