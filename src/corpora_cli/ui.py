"""Presentation layer for the ``corpora`` CLI — theme, consoles, reporters.

Everything the terminal sees is decided here so `corpora_cli.cli` can stay
about the pipeline: the accent/semantic palette, the emoji vocabulary, the
two consoles (stdout for command output, stderr for narration), the live
task list that tracks the conversion checkpoints, and the palette bridge that
repaints Typer's rich help.

The rules the rest of the package relies on:

- Accent ``#D2A24C`` marks *structure* — headings, argument names, help
  sections, the running step. Semantics get colour of their own: green
  success, yellow warning, blue info, red error.
- Nothing here is required for the CLI to work. Rich drops colour on a
  non-tty and honours ``NO_COLOR``, and every live surface has a plain
  line-oriented fallback (see `ConversionReporter`), so piped and CI output
  stays the same greppable text it has always been.
"""

from __future__ import annotations

import sys
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager, redirect_stdout
from types import TracebackType
from typing import TYPE_CHECKING, Any

from rich import box
from rich.console import Console, RenderableType
from rich.progress import Progress, ProgressColumn, TaskID, TextColumn
from rich.spinner import Spinner
from rich.table import Table
from rich.text import Text
from rich.theme import Theme
from rich.tree import Tree

if TYPE_CHECKING:  # pragma: no cover - typing only
    from rich.progress import Task

ACCENT = "#D2A24C"

THEME = Theme(
    {
        # Structure: the brand accent, never used to mean "good" or "bad".
        "accent": ACCENT,
        "heading": f"bold {ACCENT}",
        "arg": ACCENT,
        # Semantics.
        "success": "green",
        "warning": "yellow",
        "info": "blue",
        "error": "bold red",
        "muted": "dim",
        "code": "grey85 on grey15",
        # Rich's own style names, repainted in the CLI's palette.
        "prompt.choices": ACCENT,
        "prompt.default": "dim",
        "prompt.invalid": "red",
        # Step states in the conversion task list.
        "step.running": ACCENT,
        "step.done": "green",
        "step.failed": "red",
        "step.pending": "dim",
    }
)

# One emoji per status so a scrolled-back run reads by shape, not by colour
# (which is gone the moment the output is piped to a file).
OK = "✅"
WARN = "⚠️"
ERR = "❌"
INFO = "ℹ️"
RUN = "⚙️"
TITLE = "📖"
LIBRARY = "📚"
PUBLISH = "🚀"
DOWNLOAD = "📥"
TRASH = "🗑️"
SECTIONS = "🧭"
PASSAGE = "📄"

# stderr=True resolves sys.stderr per print, so pytest's capsys still
# captures; highlight=False keeps Rich from colouring numbers inside log
# lines, soft_wrap keeps paths greppable on one line.
err = Console(stderr=True, highlight=False, soft_wrap=True, theme=THEME)
out = Console(highlight=False, soft_wrap=True, theme=THEME)


def log(message: str, style: str | None = None) -> None:
    """Narrate to stderr.

    The style rides on a `Text`, not on the print call: a styled ``print``
    tints everything it emits — including the live task list Rich redraws
    underneath it — and the message is wrapped verbatim, so user data that
    happens to contain "[" is never read as markup.
    """
    err.print(Text(message, style=style or ""))


def note(message: str) -> None:
    log(message, style="muted")


def info(message: str) -> None:
    log(f"{INFO} {message}", style="info")


def success(message: str, *, emoji: str = OK) -> None:
    """Green, with ✅ unless the command has a sign of its own (🚀, 🗑️, 📥)."""
    log(f"{emoji} {message}", style="success")


def warn(message: str) -> None:
    log(f"{WARN} warning: {message}", style="warning")


def error(message: str) -> None:
    log(f"{ERR} error: {message}", style="error")


def install_traceback() -> None:
    """Render uncaught exceptions through Rich.

    Failures the CLI knows about print one red line (``error: …``); this is
    for the ones nobody planned for — a converter bug, a malformed archive —
    where the frames are the whole point of the output. Locals stay hidden
    on purpose: a storage token in scope would otherwise land in the user's
    scrollback and, from there, in a bug report.
    """
    from rich.traceback import install

    install(console=err, show_locals=False, width=None, word_wrap=True)


def fail(message: str) -> SystemExit:
    """Report a usage error and build the `SystemExit` that ends the run.

    Exit 2 is the request being wrong — a path that isn't there, a format
    that can't be guessed, a command that isn't yours to run — as distinct
    from exit 1, work that was attempted and failed. Click already uses 2
    for its own usage errors; this keeps the rest of the CLI honest
    about the same line.

    The message is printed here rather than carried on the exception:
    ``SystemExit("…")`` would have Python print it raw, after Rich is out of
    the picture, and it would be the one uncoloured line in the CLI.
    """
    error(message)
    return SystemExit(2)


# ── typer help ───────────────────────────────────────────────────────────────
# Typer renders help through Rich itself (panelled options, wrapped text);
# the module-level style constants in `typer.rich_utils` are how its palette
# is repainted in ours. They are internal API, so every assignment is
# guarded: a Typer that renames one simply keeps its default for that piece
# rather than breaking the CLI.

_TYPER_HELP_STYLES: dict[str, str] = {
    "STYLE_OPTION": f"bold {ACCENT}",
    "STYLE_ARGUMENT": f"bold {ACCENT}",
    "STYLE_COMMAND": f"bold {ACCENT}",
    "STYLE_SWITCH": f"bold {ACCENT}",
    "STYLE_METAVAR": "dim",
    "STYLE_METAVAR_SEPARATOR": "dim",
    "STYLE_USAGE": f"bold {ACCENT}",
    # Readable body text: Typer dims all help text by default.
    "STYLE_HELPTEXT": "",
    "STYLE_OPTION_DEFAULT": "dim",
    "STYLE_REQUIRED_SHORT": "bold red",
    "STYLE_REQUIRED_LONG": "bold red",
}


def style_typer_help() -> None:
    """Repaint Typer's rich help in the CLI's accent palette."""
    try:
        from typer import rich_utils
    except ImportError:  # pragma: no cover - typer always present in practice
        return
    for constant, style in _TYPER_HELP_STYLES.items():
        if hasattr(rich_utils, constant):
            setattr(rich_utils, constant, style)


# ── conversion task list ─────────────────────────────────────────────────────
# The steps a conversion moves through, in order. The labels are ours; the
# checkpoint strings that advance them belong to
# `admin.services.conversion.run_conversion` (corpora-py, versioned
# separately), so an unrecognised message is logged rather than swallowed and
# a step left mid-flight is closed out when the reporter exits.

_STEPS: tuple[str, ...] = (
    "Parse source and build the Text-Fabric dataset",
    "Compile the cache and package the .corpus archive",
    "Validate the converted corpus",
)

_CHECKPOINTS: tuple[tuple[str, int], ...] = (
    ("Parsing ", 0),
    ("Inspecting ZIP", 0),
    ("Extracting TEI documents", 0),
    ("Text-Fabric dataset ready.", 1),
    ("Validating converted corpus", 2),
)

_COMPLETE = "Conversion complete."


def _checkpoint(message: str) -> int | None:
    for prefix, index in _CHECKPOINTS:
        if message.startswith(prefix):
            return index
    return None


class _StepColumn(ProgressColumn):
    """Spinner while a step runs, ✅ when it lands, ❌ when it fails.

    Fixed five cells wide (an emoji is two) so the labels stay aligned as
    steps flip between states, and the list sits in from the log lines above
    it rather than butting against the margin.
    """

    def __init__(self) -> None:
        super().__init__()
        self._spinner = Spinner("dots", style="step.running")

    def render(self, task: Task) -> RenderableType:
        state = task.fields.get("state", "pending")
        if state == "done":
            return Text(f"  {OK} ")
        if state == "failed":
            return Text(f"  {ERR} ")
        if state == "running":
            return Text.assemble("  ", self._spinner.render(task.get_time()), "  ")
        return Text("  ·  ", style="step.pending")


class ConversionReporter:
    """Progress surface for `run_conversion`'s callbacks.

    On a live terminal the steps render as a task list — spinner on the one
    running, ✅ behind the ones that landed — with converter warnings and
    Text-Fabric's own chatter printed above it. Anywhere else (a pipe, CI,
    the test suite) it degrades to the plain stderr lines this CLI has always
    emitted, upstream's wording included.
    """

    def __init__(self, console: Console | None = None, *, live: bool | None = None):
        self._console = console or err
        self._live = self._console.is_terminal if live is None else live
        self._progress: Progress | None = None
        self._tasks: list[TaskID] = []
        self._current: int | None = None

    @property
    def live(self) -> bool:
        """True when the steps render as a task list rather than log lines."""
        return self._live

    def __enter__(self) -> ConversionReporter:
        if not self._live:
            return self
        self._progress = Progress(
            _StepColumn(),
            TextColumn("{task.description}"),
            console=self._console,
            # The converters chatter on stdout; Live reprints it above the
            # task list, which is why `cli` hands stdout to us untouched.
            redirect_stdout=True,
            redirect_stderr=False,
        )
        # A blank line above and below the list (see __exit__) keeps it from
        # colliding with the converters' log chatter.
        self._console.line()
        self._progress.start()
        self._tasks = [
            self._progress.add_task(
                _step_label(label, "pending"), total=1, start=False, state="pending"
            )
            for label in _STEPS
        ]
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> bool:
        # Never leave a spinner mid-flight: whatever was running either
        # finished with the pipeline or died with it.
        self._settle("failed" if exc_type is not None else "done")
        if self._progress is not None:
            self._progress.stop()
            self._progress = None
            self._console.line()
        return False

    # -- run_conversion callbacks --------------------------------------------

    def title(self, display_name: str) -> None:
        self._say(f"{TITLE} Title: {display_name}", style="heading")

    def log(self, message: str) -> None:
        if message.startswith(_COMPLETE):
            self._settle("done")
            self._say(f"{OK} {message}", style="success")
            return
        index = _checkpoint(message)
        if index is None:
            # Converter warnings (downgraded categories, skipped OCR pages)
            # come through the same callback — always show them.
            self._say(f"{WARN} {message}", style="warning")
            return
        self._advance(index)
        if not self._live:
            self._say(f"{RUN} {message}", style="info")

    # -- internals -----------------------------------------------------------

    def _advance(self, index: int) -> None:
        for earlier in range(index):
            self._set_state(earlier, "done")
        self._set_state(index, "running")
        self._current = index

    def _settle(self, state: str) -> None:
        if self._current is None:
            return
        for earlier in range(self._current):
            self._set_state(earlier, "done")
        self._set_state(self._current, state)
        self._current = None

    def _set_state(self, index: int, state: str) -> None:
        if self._progress is None or index >= len(self._tasks):
            return
        self._progress.update(
            self._tasks[index],
            description=_step_label(_STEPS[index], state),
            state=state,
            completed=1 if state in ("done", "failed") else 0,
            # Redraw now: a log line printed straight after this would
            # otherwise reprint the task list as it stood at the last tick.
            refresh=True,
        )

    def _say(self, message: str, *, style: str) -> None:
        # Rich routes prints above an active Live region, so this is safe
        # while the task list is on screen (see `log` on styling the `Text`
        # rather than the print).
        self._console.print(Text(message, style=style))


def _step_label(label: str, state: str) -> str:
    return f"[step.{state}]{label}[/step.{state}]"


# ── tables, trees, spinners ──────────────────────────────────────────────────
# Cell contents are wrapped in `Text` on the way in: corpus data (passages,
# manifest values, filenames) is not markup and a stray "[" must not be
# read as a style tag.


def heading(text: str, *, gap: int = 2) -> None:
    """Print an accent heading to stdout, with blank lines above it.

    The space belongs to the heading rather than the caller: every block in
    the output is introduced the same way, so they stay evenly separated
    however they are combined. Two blank lines by default — a table runs
    right up against the next heading with only one — and the first heading
    of a command's output passes ``gap=1``.
    """
    out.line(gap)
    out.print(Text(text, style="heading"))


def table(*headers: str, justify: Sequence[str] | None = None) -> Table:
    """A borderless table with an accent header rule.

    Columns are given a two-space gutter and the rows a half-line of air:
    terminal output is read in a glance, and cramped columns are the first
    thing to make one unreadable.
    """
    built = Table(
        box=box.SIMPLE_HEAD,
        pad_edge=False,
        show_edge=False,
        header_style="heading",
        padding=(0, 2),
    )
    for index, header in enumerate(headers):
        built.add_column(
            header,
            justify=justify[index] if justify else "left",  # type: ignore[arg-type]
            overflow="fold",
        )
    return built


def key_value_table(mapping: Mapping[str, Any], *, key_header: str, value_header: str) -> Table:
    built = table(key_header, value_header)
    built.columns[0].style = "accent"
    built.columns[0].no_wrap = True
    for key, value in mapping.items():
        built.add_row(Text(str(key)), Text(str(value)))
    return built


def passage_table(passages: Sequence[Any]) -> Table:
    """Passages as `corpus_detail.get_content` returns them, or plain strings.

    Dicts carry ``ref``/``text`` — the ref column keeps every passage
    addressable (``corpora library show … --ref <ref>`` again, deeper). A
    plain-string sequence renders without the ref column.
    """
    rows: list[tuple[str, str]] = []
    for passage in passages:
        if isinstance(passage, Mapping):
            text = str(passage.get("text") or "")
            if text:
                rows.append((str(passage.get("ref") or ""), text))
        elif str(passage):
            rows.append(("", str(passage)))
    with_refs = any(ref for ref, _ in rows)
    if with_refs:
        built = table("ref", "passage")
        built.columns[0].style = "accent"
        built.columns[0].no_wrap = True
    else:
        built = table("#", "passage", justify=("right", "left"))
        built.columns[0].style = "muted"
    for index, (ref, text) in enumerate(rows, start=1):
        built.add_row(Text(ref) if with_refs else Text(str(index)), Text(text))
    return built


def node_type_table(rows: Sequence[Mapping[str, Any]]) -> Table:
    """Per-otype stats — the ``node_types`` list `corpus_detail.get_index`
    returns (``type``/``count``/``avg_slots``/``is_slot``).

    The slot type is the corpus's atom (usually words); everything else
    spans slots, and the average span is what tells a book from a verse at
    a glance.
    """
    built = table("node type", "count", "avg span", justify=("left", "right", "right"))
    built.columns[0].style = "accent"
    for row in rows:
        name = str(row.get("type") or "")
        if row.get("is_slot"):
            name = f"{name} (slot)"
        count = row.get("count")
        avg = row.get("avg_slots")
        built.add_row(
            Text(name),
            Text(f"{count:,}" if isinstance(count, int) else str(count or "")),
            Text("—" if row.get("is_slot") else str(avg if avg is not None else "")),
        )
    return built


def print_validation(summary: Mapping[str, Any], console: Console | None = None) -> None:
    """Render a validation summary as a verdict panel on stderr.

    The panel border carries the verdict colour and the first line the
    verdict text ("Validation: valid" / "Validation: INVALID"), so a piped,
    colourless run still reads unambiguously. Stats become a key/value
    table; failure reasons are listed inside the panel, each with ❌.
    """
    from rich.console import Group
    from rich.panel import Panel

    valid = bool(summary.get("valid"))
    verdict_style = "success" if valid else "error"
    parts: list[RenderableType] = [
        Text(
            f"{OK} Validation: valid" if valid else f"{ERR} Validation: INVALID",
            style=verdict_style,
        )
    ]
    stats = summary.get("stats") or {}
    if stats:
        parts.append(key_value_table(stats, key_header="stat", value_header="value"))
    for reason in summary.get("reasons") or []:
        parts.append(Text(f"{ERR} reason: {reason}", style="error"))
    target = console or err
    target.print(
        Panel(
            Group(*parts),
            title=Text("corpus validation", style="heading"),
            border_style=verdict_style,
            box=box.ROUNDED,
            expand=False,
            padding=(0, 2),
        )
    )


def section_tree(items: Sequence[Mapping[str, Any]]) -> Tree:
    """Two-level section index — the shape `corpus_detail` returns.

    The root is hidden: the caller has already printed the heading.
    """
    tree = Tree("sections", hide_root=True, guide_style="muted")
    for item in items:
        node = tree.add(Text(str(item.get("title") or item.get("ref") or ""), style="accent"))
        for child in item.get("children") or []:
            node.add(Text(str(child.get("title") or child.get("ref") or "")))
        if item.get("truncated"):
            # The index caps children per node; the ellipsis says "there is
            # more" without pretending to know how much.
            node.add(Text("…", style="muted"))
    return tree


def block(renderable: Any, console: Console | None = None) -> None:
    """Print a table or tree that has no heading, with air above it.

    Blocks that *do* have a heading get their space from `heading`; between
    them the output keeps exactly one blank line either way.
    """
    target = console or out
    target.line()
    target.print(renderable)


def command_hint(command: str, console: Console | None = None) -> None:
    """A follow-up command the reader can copy, set off like a code block.

    Guidance, not output — it goes to stderr by default so piping a
    command's real output never picks up the hint. The block background is
    what says "this is a command"; piped and NO_COLOR output degrades to
    the bare command line, still copyable.
    """
    from rich.padding import Padding

    target = console or err
    target.line()
    target.print(Padding(Text(command, style="code"), (0, 2), style="code", expand=False))


@contextmanager
def spinner(message: str) -> Iterator[None]:
    """Spin on a live terminal while a blocking call runs.

    Either way stdout is quarantined for the duration — the backends and
    text-fabric chatter on it, and only a command's real result may reach
    stdout (Rich's Live does the quarantining itself, by reprinting the
    chatter above the spinner).
    """
    if err.is_terminal:
        with err.status(Text(message, style="accent"), spinner="dots", spinner_style=ACCENT):
            yield
    else:
        with redirect_stdout(sys.stderr):
            yield
