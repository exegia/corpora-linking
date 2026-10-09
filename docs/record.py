"""Regenerate the README's terminal shots (``docs/*.svg``).

Every shot is a real Rich console recording, drawn by `docs/terminal_svg.py`
in the design's terminal format, so the images can't drift from the styling
the CLI actually prints — re-run this after touching `ui` and the README
updates itself.

``corpora --help`` / ``convert --help`` / ``validate`` are real runs: the
validate shot converts a throwaway document first and reports its real
stats. The ``library`` shots run against a small stand-in backend so that
recording them never depends on anyone's storage credentials.

The conversion GIF is the one thing not made here — a spinner and a task
list ticking over need a real terminal; see ``docs/convert.tape`` (vhs).

Usage::

    make docs          # this script, then the vhs tape
    uv run python docs/record.py
"""

from __future__ import annotations

import contextlib
import os
import sys
import tempfile
from pathlib import Path

DOCS = Path(__file__).resolve().parent
sys.path.insert(0, str(DOCS))

WIDTH = 88

# The shots are rendered by docs/terminal_svg.py — Rich's own exporter has its
# type size, line height and padding baked in, and the design calls for a
# different grid.
import terminal_svg  # noqa: E402
from rich.console import Console  # noqa: E402

# Help output wraps to $COLUMNS; keep it at the recorded width.
os.environ["COLUMNS"] = str(WIDTH)

from corpora_cli import cli, ui  # noqa: E402

SAMPLE = """The Art of War

Chapter 1. Laying Plans

Sun Tzu said: The art of war is of vital importance to the State. It is a
matter of life and death, a road either to safety or to ruin. Hence it is a
subject of inquiry which can on no account be neglected.

Chapter 2. Waging War

In the operations of war, where there are in the field a thousand swift
chariots, as many heavy chariots, and a hundred thousand mail-clad soldiers,
provisions enough to carry them a thousand li, the expenditure at home and at
the front will reach a thousand ounces of silver a day.
"""


class _Stored:
    def __init__(self, filename: str, megabytes: float):
        self.filename = filename
        self.size_bytes = int(megabytes * 1024 * 1024)
        self.repo_id = "exegia/corpus-archives"
        self.url = f"https://huggingface.co/exegia/corpus-archives/{filename}"


class _Storage:
    def list(self):
        return [
            _Stored("the-art-of-war.corpus", 0.4),
            _Stored("moby-dick.corpus", 2.0),
            _Stored("summa-theologiae-1265.corpus", 41.7),
        ]


_MANIFEST = {
    "name": "Moby Dick",
    "category": "book",
    "source_format": "epub",
    "created": "2026-08-26T09:41:12Z",
    "corpus_version": "1.0",
}

_INDEX = {
    "sections": {
        "levels": ["book", "chapter"],
        "items": [
            {
                "title": "Loomings",
                "ref": "Moby Dick 1",
                "children": [
                    {"title": "Moby Dick 1:1", "ref": "Moby Dick 1:1"},
                    {"title": "Moby Dick 1:2", "ref": "Moby Dick 1:2"},
                ],
            },
            {"title": "The Carpet-Bag", "ref": "Moby Dick 2", "children": []},
            {"title": "The Spouter-Inn", "ref": "Moby Dick 3", "children": []},
        ],
    },
    "node_types": [
        {"type": "book", "count": 1, "avg_slots": 208642.0, "is_slot": False},
        {"type": "chapter", "count": 135, "avg_slots": 1545.5, "is_slot": False},
        {"type": "paragraph", "count": 2694, "avg_slots": 77.4, "is_slot": False},
        {"type": "word", "count": 208642, "avg_slots": 1.0, "is_slot": True},
    ],
}

_CONTENT = {
    "total": 42,
    "passages": [
        {
            "ref": "Moby Dick 1:1",
            "text": "Call me Ishmael. Some years ago—never mind how long precisely—having "
            "little or no money in my purse, and nothing particular to interest me on "
            "shore, I thought I would sail about a little and see the watery part of "
            "the world.",
        },
        {
            "ref": "Moby Dick 1:2",
            "text": "There now is your insular city of the Manhattoes, belted round by "
            "wharves as Indian isles by coral reefs—commerce surrounds it with her surf.",
        },
    ],
}


def _recorder() -> Console:
    return Console(
        record=True,
        force_terminal=True,
        width=WIDTH,
        theme=ui.THEME,
        highlight=False,
    )


@contextlib.contextmanager
def _shot(name: str):
    """Point the CLI's output at a recorder, then write ``docs/<name>.svg``."""
    from typer import rich_utils

    console = _recorder()
    saved = (ui.err, ui.out, ui.spinner)
    ui.err = ui.out = console
    # The spinner is transient on a real terminal — there is nothing left of
    # it to photograph.
    ui.spinner = lambda message: contextlib.nullcontext()
    # Typer builds its help console per call from sys.stdout; hand it the
    # recorder instead so `--help` shots carry the real styling.
    saved_factory = getattr(rich_utils, "_get_rich_console", None)
    if saved_factory is not None:
        rich_utils._get_rich_console = lambda stderr=False: console
    try:
        yield console
    finally:
        ui.err, ui.out, ui.spinner = saved
        if saved_factory is not None:
            rich_utils._get_rich_console = saved_factory
    path = terminal_svg.save(console, DOCS / f"{name}.svg")
    print(f"wrote {path.relative_to(DOCS.parent)}")


def _quietly(argv: list[str]) -> None:
    """Run a command with its narration — and the converters' — thrown away."""
    saved = (ui.err, ui.out)
    with open(os.devnull, "w") as devnull:
        ui.err = ui.out = Console(file=devnull, width=WIDTH, theme=ui.THEME)
        try:
            with contextlib.redirect_stdout(devnull):
                cli.main(argv)
        finally:
            ui.err, ui.out = saved


def main() -> int:
    with _shot("corpora-help"):
        cli.main([])

    with _shot("corpora-convert-help"):
        with contextlib.suppress(SystemExit):
            cli.main(["convert", "--help"])

    with tempfile.TemporaryDirectory(prefix="corpora-docs-") as tmp:
        source = Path(tmp) / "The Art of War.txt"
        source.write_text(SAMPLE)
        archive = Path(tmp) / "the-art-of-war.corpus"
        _quietly(["convert", str(source), "-o", str(archive)])
        with _shot("corpora-validate"):
            cli.main(["validate", str(archive)])

    import admin.services.corpus_detail as detail
    import admin.services.storage as storage

    storage.make_corpus_storage = lambda: _Storage()
    detail.get_manifest = lambda filename: _MANIFEST
    detail.get_index = lambda filename: _INDEX
    detail.get_content = lambda filename, ref=None: _CONTENT

    with _shot("corpora-library-list"):
        cli.main(["library", "list"])

    with _shot("corpora-library-show"):
        cli.main(["library", "show", "moby-dick.corpus", "--ref", "Moby Dick 1"])

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
