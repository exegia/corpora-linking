"""``corpora`` — terminal CLI over the corpora-py conversion pipeline.

Runs the exact same pipeline as corpora-api's ``POST /convert`` — upload
gate → parse → Text-Fabric → ``.cfm`` → ``.corpus`` → validation gate —
but locally and synchronously, with no server, jobs, or transport
involved: everything goes through the transport-free seams of the
corpora-py distribution (`admin.services.*`), which this package depends
on from PyPI.

The command surface is a Typer app rendered through Rich — panelled help
with the accent palette, a live task list while a conversion runs, verdict
panels for validation, tables and a section tree for corpus exploration
(see `corpora_cli.ui`) — but stays line-oriented and scriptable: plain
subcommands, no interactive screens or TUI.

Usage::

    corpora convert mobydick.epub --name "Moby Dick" -o mobydick.corpus
    corpora convert dataset.zip --format tf_zip
    corpora validate mobydick.corpus
    corpora schema mobydick.epub -o mobydick.schema.json
    corpora reconcile mobydick.corpus --schema mobydick.schema.json --yes
    corpora library list
    corpora library show mobydick.corpus --ref "Moby Dick 1"

``schema`` and ``reconcile`` answer a different question than ``validate``:
not "is the archive internally sound?" but "does it faithfully represent the
document it was converted from?" (issue #41). ``schema`` normalises the
source document into a reference schema; ``reconcile`` aligns that schema
with the archive's Text-Fabric sections — bridging the two sides' level
names with an explicit or inferred ``--map`` — and reports what the
conversion lost.

`library publish`, `download` and `delete` write to shared storage on a
person's behalf; they are hidden and locked until the CLI can sign in (see
`AUTH_ISSUE`).

Exit codes: 0 success; 1 work that was attempted and failed — a failed
conversion, a corpus that fails its integrity checks, a storage error; 2 a
request that was wrong — an unreadable source, an unknown format, a refused
overwrite, unconfigured storage, a locked command, the pre-conversion upload
gate, Click's own usage errors, and a bare `corpora`.

Scripting contract: the only stdout of ``convert`` is the result path
(``corpus=$(corpora convert ...)``); logs and decoration go to stderr.
``library list``/``show`` print their tables to stdout — they *are* the
command's output — and degrade to plain text when piped (Rich drops
colour on a non-tty; ``NO_COLOR`` is honoured).
"""

from __future__ import annotations

import contextlib
import gc
import json
import re
import sys
import tempfile
import zipfile
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

import typer

try:
    # Typer >= 0.24 vendors Click; the app's commands raise *these* exception
    # classes, so the standalone `click` package (a different module even
    # when installed) must not be the one caught here.
    from typer._click import exceptions as click_exceptions
    from typer._click.core import Context as ClickContext
except ImportError:  # pragma: no cover - older Typer with real Click
    from click import Context as ClickContext  # type: ignore[assignment]
    from click import exceptions as click_exceptions  # type: ignore[no-redef]
from admin.parsers.schema import CorpusCategory, SourceFormat
from admin.services.conversion import (
    ConversionError,
    CorpusValidationError,
    run_conversion,
    validate_archive,
)
from admin.services.upload_validation import validate_upload

from corpora_cli import ui

ui.style_typer_help()

app = typer.Typer(
    name="corpora",
    help="Convert documents into queryable .corpus text archives, using "
    "the same pipeline as the corpora-api /convert endpoint.",
    rich_markup_mode="rich",
    add_completion=True,
    # Uncaught exceptions are handled by `ui.install_traceback` (locals
    # hidden); Typer's own pretty-exceptions layer would double-render them.
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)

library_app = typer.Typer(
    name="library",
    help="Explore and manage stored archives on the configured backend.",
    rich_markup_mode="rich",
    pretty_exceptions_enable=False,
    context_settings={"help_option_names": ["-h", "--help"]},
)
app.add_typer(library_app)

# Extension → format for the unambiguous cases. `.zip` is deliberately
# absent: magic bytes and extensions can't tell a Text-Fabric dataset ZIP
# from a TEI-document ZIP, so ZIP sources must pass an explicit --format.
_EXTENSION_FORMATS: dict[str, SourceFormat] = {
    ".epub": SourceFormat.EPUB,
    ".html": SourceFormat.HTML,
    ".htm": SourceFormat.HTML,
    ".xhtml": SourceFormat.HTML,
    ".xml": SourceFormat.XML,
    ".tei": SourceFormat.TEI,
    ".pdf": SourceFormat.PDF,
    ".txt": SourceFormat.PLAIN,
    ".text": SourceFormat.PLAIN,
    ".md": SourceFormat.PLAIN,
    ".markdown": SourceFormat.PLAIN,
}


class SchemaFormat(StrEnum):
    """Reference formats `corpora_cli.reconcile.docschema` can extract."""

    AUTO = "auto"
    EPUB = "epub"
    XML = "xml"
    MARKDOWN = "markdown"


def _human_size(size_bytes: int) -> str:
    if size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes / (1024 * 1024):.1f} MB"


def _release_command_resources() -> None:
    """Release resources held by the third-party corpus loaders.

    Text-Fabric and Context-Fabric do not currently expose a common close
    lifecycle. Context-Fabric's mmap-backed API also contains reference cycles,
    so merely returning from a command leaves its mapped files open until
    Python happens to collect them. The CLI is an invocation boundary rather
    than a long-lived corpus session; collect here so repeated in-process
    ``main()`` calls (as used by the test suite and embedding callers) have
    the same resource behavior as separate CLI processes.
    """
    gc.collect()


def _slugify(name: str) -> str:
    """Kebab-case a display name for the default output filename.

    Mirrors the server's result-filename slugging (`jobs._slugify`) without
    importing `jobs` — that module spins up the `JobManager` thread pool at
    import time, which a one-shot CLI has no use for.
    """
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")


def _infer_format(source: Path, declared: SourceFormat | str | None) -> SourceFormat:
    if declared:
        return SourceFormat(declared)
    suffix = source.suffix.lower()
    if suffix == ".zip":
        raise ui.fail(
            "a .zip source is ambiguous — pass --format tf_zip "
            "(a Text-Fabric dataset) or --format tei_zip (TEI documents)."
        )
    inferred = _EXTENSION_FORMATS.get(suffix)
    if inferred is None:
        known = ", ".join(sorted(_EXTENSION_FORMATS))
        raise ui.fail(
            f"cannot infer a source format from '{source.name}' — "
            f"pass --format (recognized extensions: {known}, .zip with an "
            "explicit --format)."
        )
    return inferred


# ── convert / validate ───────────────────────────────────────────────────────


@app.command()
def convert(
    source: Annotated[
        Path, typer.Argument(help="Path to the source document.", show_default=False)
    ],
    source_format: Annotated[
        SourceFormat | None,
        typer.Option(
            "--format",
            "-f",
            help="Source format (inferred from the file extension when "
            "omitted; required for .zip sources).",
            show_default=False,
        ),
    ] = None,
    name: Annotated[
        str,
        typer.Option("--name", "-n", help="Corpus name (fallback when the source has no title)."),
    ] = "",
    description: Annotated[
        str, typer.Option("--description", "-d", help="Corpus description.")
    ] = "",
    category: Annotated[
        CorpusCategory | None,
        typer.Option(
            "--category",
            "-c",
            help="Corpus structure category (auto-detected when omitted; an "
            "upgrade the document tree can't support is downgraded with a "
            "warning).",
            show_default=False,
        ),
    ] = None,
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Output .corpus path (default: ./<slugified-title>.corpus).",
            show_default=False,
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite the output file if it already exists.")
    ] = False,
) -> None:
    """Convert a source document into a .corpus archive."""
    source = source.expanduser()
    if not source.is_file():
        raise ui.fail(f"source file not found: {source}")
    resolved_format = _infer_format(source, source_format)

    # The same pre-conversion gate as POST /convert (issue #173): reject
    # obviously non-convertible bytes before spending minutes converting.
    report = validate_upload(source, resolved_format)
    for warning in report.warnings:
        ui.warn(warning)
    if not report.convertible:
        for reason in report.reasons:
            ui.error(reason)
        raise SystemExit(2)

    def output_path_for(display_name: str) -> Path:
        if output:
            path = output.expanduser()
        else:
            stem = _slugify(display_name) or _slugify(source.stem) or "corpus"
            path = Path.cwd() / f"{stem}.corpus"
        if path.exists() and not force:
            raise ui.fail(f"{path} already exists — pass --force to overwrite.")
        path.parent.mkdir(parents=True, exist_ok=True)
        return path

    # A live task list on a terminal; piped/CI output stays plain lines.
    reporter = ui.ConversionReporter()
    # text-fabric/cfabric print progress chatter straight to stdout; it has
    # to go to stderr so the only stdout line is the result path (the
    # scripting contract: `corpus=$(corpora convert ...)`). The live
    # reporter redirects stdout itself — reprinting the chatter above its
    # task list — so only the plain path wires the redirect by hand.
    stdout_cm: contextlib.AbstractContextManager[Any] = (
        contextlib.nullcontext() if reporter.live else contextlib.redirect_stdout(sys.stderr)
    )

    result: Path | None = None
    with tempfile.TemporaryDirectory(prefix="corpora-cli-") as tmp:
        try:
            with reporter, stdout_cm:
                result = run_conversion(
                    source_path=source,
                    work_dir=Path(tmp) / "work",
                    source_format=resolved_format,
                    output_path_for=output_path_for,
                    name=name,
                    description=description,
                    category=category,
                    on_log=reporter.log,
                    on_display_name=reporter.title,
                    on_validation=lambda summary: ui.print_validation(summary),
                )
        except CorpusValidationError as exc:
            # The archive was already written before the gate ran — keep it
            # (the user may want to inspect it) but fail the command.
            ui.error(str(exc))
            raise typer.Exit(1) from exc
        except ConversionError as exc:
            ui.error(str(exc))
            raise typer.Exit(1) from exc
        finally:
            # Release mmap-backed loader state before the temporary corpus
            # directory is removed, including when the command is called
            # directly rather than through `main()`.
            _release_command_resources()

    assert result is not None
    ui.note(f"  {result.name} — {_human_size(result.stat().st_size)}")
    print(result)


@app.command()
def validate(
    corpus: Annotated[Path, typer.Argument(help="Path to a .corpus archive.", show_default=False)],
) -> None:
    """Run the corpus integrity checks over a .corpus file."""
    archive = corpus.expanduser()
    if not archive.is_file():
        raise ui.fail(f"corpus file not found: {archive}")
    try:
        summary = validate_archive(archive)
    finally:
        _release_command_resources()
    ui.print_validation(summary)
    if not summary.get("valid"):
        raise typer.Exit(1)


# ── schema / reconcile ───────────────────────────────────────────────────────
# Source-fidelity checks (issue #41): `schema` extracts a reference schema
# from the original document, `reconcile` aligns it with a converted
# archive's Text-Fabric sections. Both are stdlib-only
# (`corpora_cli.reconcile`) so they work on archives the heavy loaders
# cannot open.


@app.command(
    help="Extract a reference schema (levels, units, word shingles) from "
    "a source document, for `corpora reconcile`."
)
def schema(
    document: Annotated[
        Path,
        typer.Argument(help="Source document (epub, tei/xml, markdown, txt).", show_default=False),
    ],
    schema_format: Annotated[
        SchemaFormat,
        typer.Option(
            "--format",
            "-f",
            help="Reference format (inferred from the extension when omitted).",
        ),
    ] = SchemaFormat.AUTO,
    levels: Annotated[
        str,
        typer.Option(
            help="Comma-separated level names, outermost first (overrides detection).",
            show_default=False,
        ),
    ] = "",
    output: Annotated[
        Path | None,
        typer.Option(
            "--output",
            "-o",
            help="Schema JSON path (default: ./<stem>.schema.json).",
            show_default=False,
        ),
    ] = None,
    force: Annotated[
        bool, typer.Option("--force", help="Overwrite the output file if it already exists.")
    ] = False,
) -> None:
    from xml.etree import ElementTree

    from corpora_cli.reconcile import docschema

    document = document.expanduser()
    if not document.is_file():
        raise ui.fail(f"document not found: {document}")
    target = output.expanduser() if output else (Path.cwd() / f"{document.stem}.schema.json")
    if target.exists() and not force:
        raise ui.fail(f"{target} already exists — pass --force to overwrite.")

    level_names = [part.strip() for part in levels.split(",") if part.strip()]
    try:
        extracted = docschema.extract(str(document), schema_format.value, level_names)
    except docschema.SchemaError as exc:
        raise ui.fail(str(exc)) from exc
    except (ElementTree.ParseError, zipfile.BadZipFile, OSError) as exc:
        ui.error(f"cannot read {document.name}: {exc}")
        raise typer.Exit(1) from exc

    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(extracted, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    title = extracted.get("title") or "(untitled)"
    ui.success(f"Schema: {extracted['source']} [{extracted['format']}] — {title}")
    ui.note(f"  levels: {' > '.join(extracted['levels'])}")
    for level, count in extracted["unit_counts"].items():
        ui.note(f"  {level}: {count} unit(s)")
    ui.note(f"  total words: {extracted['total_words']:,}")
    print(target)


def _locate_tf_dir(corpus: Path, extract_root: Path) -> Path:
    """Find the directory holding ``otype.tf`` — inside `corpus` if it is a
    directory, else inside the archive extracted to `extract_root`."""
    if corpus.is_dir():
        root = corpus
    else:
        try:
            with zipfile.ZipFile(corpus) as archive:
                archive.extractall(extract_root)
        except zipfile.BadZipFile as exc:
            raise ui.fail(f"{corpus} is neither a .tf directory nor a readable archive.") from exc
        root = extract_root
    if (root / "otype.tf").is_file():
        return root
    hits = sorted(root.rglob("otype.tf"), key=lambda p: len(p.parts))
    if not hits:
        raise ui.fail(f"no otype.tf found under {corpus} — not a Text-Fabric corpus.")
    return hits[0].parent


@app.command(
    help="Compare a converted corpus against its source document's "
    "reference schema and report what the conversion lost."
)
def reconcile(
    corpus: Annotated[
        Path,
        typer.Argument(
            help="A .corpus archive, a Text-Fabric .zip, or a directory of .tf files.",
            show_default=False,
        ),
    ],
    schema_path: Annotated[
        Path,
        typer.Option(
            "--schema",
            help="Reference schema JSON from `corpora schema`.",
            show_default=False,
        ),
    ],
    level_map: Annotated[
        list[str] | None,
        typer.Option(
            "--map",
            metavar="REF=CORPUS",
            help="Alias one reference level to one corpus section type "
            "(e.g. --map h1=book --map h2=chapter); repeatable. All mapped "
            "levels are compared pairwise. Without it the mapping is "
            "inferred and must be confirmed.",
            show_default=False,
        ),
    ] = None,
    map_file: Annotated[
        str,
        typer.Option(
            help="Persisted level mapping (JSON): read when it exists (unless "
            "--map is given), and the confirmed mapping is written back to it.",
            show_default=False,
        ),
    ] = "",
    yes: Annotated[
        bool,
        typer.Option(
            "--yes",
            "-y",
            help="Accept the inferred level mapping without prompting (for scripts and CI).",
        ),
    ] = False,
    level: Annotated[
        str,
        typer.Option(
            help="Corpus section type to compare (single-level escape hatch).",
            show_default=False,
        ),
    ] = "",
    ref_level: Annotated[
        str,
        typer.Option(
            help="Reference level to compare (single-level escape hatch).",
            show_default=False,
        ),
    ] = "",
    label_feature: Annotated[
        str,
        typer.Option(
            help="Node feature holding the section label (default: the matching sectionFeature).",
            show_default=False,
        ),
    ] = "",
    tolerance: Annotated[
        int,
        typer.Option(help="Slot offset tolerated before a boundary counts as drifted."),
    ] = 3,
    report_path: Annotated[
        str,
        typer.Option(
            "--report", help="Write the Markdown report to this path.", show_default=False
        ),
    ] = "",
    json_out: Annotated[
        str,
        typer.Option(
            "--json", help="Write the machine-readable result to this path.", show_default=False
        ),
    ] = "",
    patch_dir: Annotated[
        str,
        typer.Option(
            help="Write append-only .tf patches for missing units to this directory.",
            show_default=False,
        ),
    ] = "",
    quiet: Annotated[
        bool, typer.Option("--quiet", "-q", help="Suppress the report on stdout.")
    ] = False,
) -> None:
    from corpora_cli import reconcile as reconcile_mod

    corpus = corpus.expanduser()
    if not corpus.exists():
        raise ui.fail(f"corpus not found: {corpus}")
    schema_file = schema_path.expanduser()
    if not schema_file.is_file():
        raise ui.fail(
            f"schema file not found: {schema_file} — create it with `corpora schema <document>`."
        )

    def confirm(rows: list[dict], unmapped_ref: list[str], unmapped_cor: list[str]) -> bool:
        ui.info("Inferred level mapping:")
        table = ui.table("reference", "corpus", "evidence")
        for row in rows:
            table.add_row(row["ref"], row["corpus"], reconcile_mod.format_evidence(row))
        ui.block(table, ui.err)
        for name in unmapped_ref:
            ui.warn(f"unmapped reference level: {name}")
        for stype in unmapped_cor:
            ui.warn(f"unmapped corpus section type: {stype}")
        if yes:
            ui.note("Proceeding (--yes).")
            return True
        if not sys.stdin.isatty():
            return False
        from rich.prompt import Confirm

        return Confirm.ask("Proceed with this mapping?", console=ui.err)

    with tempfile.TemporaryDirectory(prefix="corpora-reconcile-") as tmp:
        tf_dir = _locate_tf_dir(corpus, Path(tmp))
        options = reconcile_mod.Options(
            corpus_dir=str(tf_dir),
            schema_path=str(schema_file),
            map_pairs=level_map or [],
            map_file=map_file,
            level=level,
            ref_level=ref_level,
            label_feature=label_feature,
            tolerance=tolerance,
            report_path=report_path,
            json_path=json_out,
            patch_dir=patch_dir,
        )
        try:
            result = reconcile_mod.run(options, confirm)
        except reconcile_mod.MappingError as exc:
            raise ui.fail(str(exc)) from exc

    if not quiet:
        print(result.report, end="")
    errors = sum(1 for finding in result.findings if finding["severity"] == "error")
    if errors:
        ui.log(f"{ui.ERR} Reconciliation: FAIL ({errors} error finding(s))", style="error")
    else:
        ui.success("Reconciliation: PASS")
    if result.exit_code:
        raise typer.Exit(result.exit_code)


# ── library ──────────────────────────────────────────────────────────────────
# The `/storage` and `/storage/{filename}/…` surfaces of corpora-api as
# plain subcommands, calling the same in-process services. Storage imports
# are lazy: the scripting subcommands must not pay for the storage backends.


def _storage():
    from admin.services.storage import make_corpus_storage

    return make_corpus_storage()


@contextlib.contextmanager
def _storage_errors():
    """Map backend failures onto the CLI's exit-code contract.

    Unconfigured storage is the request being wrong (exit 2, `ui.fail`);
    a backend that was reached and refused is work that failed (exit 1).
    """
    from admin.services.storage import StorageError, StorageNotConfiguredError

    try:
        yield
    except StorageNotConfiguredError as exc:
        raise ui.fail(f"storage not configured: {exc}") from exc
    except StorageError as exc:
        ui.error(f"storage error: {exc}")
        raise typer.Exit(1) from exc


@library_app.command("list")
def library_list() -> None:
    """List stored archives."""
    with _storage_errors():
        corpora = list(_storage().list())
    if not corpora:
        ui.info("No stored corpora.")
        return
    table = ui.table("filename", "size", "repo", justify=("left", "right", "left"))
    total_bytes = 0
    for item in corpora:
        size = _human_size(item.size_bytes) if item.size_bytes else "?"
        total_bytes += item.size_bytes or 0
        table.add_row(item.filename, size, item.repo_id)
    ui.block(table)
    total = f", {total_bytes / (1024 * 1024):.1f} MB" if total_bytes else ""
    ui.note(f"{ui.LIBRARY} {len(corpora)} stored corpora{total}.")
    ui.command_hint(f"corpora library show {corpora[0].filename}")


@library_app.command("show")
def library_show(
    filename: Annotated[str, typer.Argument(help="Stored archive filename.", show_default=False)],
    ref: Annotated[
        str,
        typer.Option(
            help="Section reference whose passages should be printed.", show_default=False
        ),
    ] = "",
) -> None:
    """Show a stored archive's manifest, structure, and section tree."""
    from admin.services.corpus_detail import get_content, get_index, get_manifest, invalidate

    # corpus_detail (via text-fabric) chatters on stdout; `ui.spinner` keeps
    # stdout clean for the rendered output.
    try:
        with _storage_errors():
            with ui.spinner(f"Reading {filename}…"):
                manifest = get_manifest(filename)
                index = get_index(filename)
                content = get_content(filename, ref=ref) if ref else None
    finally:
        # The service caches the API for server-side repeated reads. A CLI
        # invocation has no subsequent request that can benefit from it, so
        # discard the extraction and its mmap-backed API at this boundary.
        with contextlib.suppress(Exception):
            invalidate(filename)
        _release_command_resources()

    scalars = {
        key: value
        for key, value in manifest.items()
        if isinstance(value, (str, int, float, bool)) and str(value)
    }
    ui.heading(f"{ui.TITLE} manifest", gap=1)
    ui.out.print(ui.key_value_table(scalars, key_header="field", value_header="value"))

    # `sections` is `{"levels": [...], "items": [...]}` (see
    # corpus_detail._build_sections); each item carries `title`/`ref` and
    # one level of `children`.
    sections: dict[str, Any] = index.get("sections") or {}
    items = sections.get("items") or []
    ui.heading(f"{ui.SECTIONS} sections")
    ui.out.print(ui.section_tree(items))

    node_types = index.get("node_types") or []
    if node_types:
        ui.heading(f"{ui.RUN} structure")
        ui.out.print(ui.node_type_table(node_types))

    if content is None and items and items[0].get("ref"):
        # Point at the next step: reading a section's passages.
        ui.command_hint(f'corpora library show {filename} --ref "{items[0]["ref"]}"')

    if content is not None:
        passages = content.get("passages") or []
        ui.heading(f"{ui.PASSAGE} {ref}")
        ui.out.print(ui.passage_table(passages))
        total = content.get("total")
        if total and total > len(passages):
            ui.note(f"… {len(passages)} of {total} passages shown")


# Publishing, downloading and deleting act on shared storage on someone's
# behalf, so they wait for `corpora auth` (issue #28): hidden from help and
# refusing to run until the CLI knows who is asking. The real implementations
# (`_cmd_library_*` below) are kept wired for tests — registering them as the
# command bodies is what unlocks them.
AUTH_ISSUE = "https://github.com/exegia/homebrew-corpora/issues/28"

LOCKED_LIBRARY_COMMANDS = ("publish", "download", "delete")


def _locked(command: str) -> SystemExit:
    return ui.fail(
        f"`corpora library {command}` needs a signed-in corpora account, and "
        f"`corpora auth` does not exist yet — see {AUTH_ISSUE}. "
        "`corpora library list` and `corpora library show` work without one."
    )


# The locked commands are registered hidden: absent from help and from
# Click's "No such command" suggestions, but someone who knows they exist
# gets the explanation rather than a typo error. The arguments are optional
# on purpose — the lock must answer before Click can demand an argument.


@library_app.command(hidden=True)
def publish(corpus: Annotated[str, typer.Argument()] = "") -> None:
    raise _locked("publish")


@library_app.command(hidden=True)
def download(
    filename: Annotated[str, typer.Argument()] = "",
    dest: Annotated[str, typer.Option()] = "",
) -> None:
    raise _locked("download")


@library_app.command(hidden=True)
def delete(
    filename: Annotated[str, typer.Argument()] = "",
    yes: Annotated[bool, typer.Option("--yes", "-y")] = False,
) -> None:
    raise _locked("delete")


def _cmd_library_publish(corpus: str) -> None:
    path = Path(corpus).expanduser()
    if not path.is_file():
        raise ui.fail(f"local corpus not found: {path}")
    # `ui.spinner` also keeps the backend off stdout — only the URL below
    # belongs there.
    with _storage_errors():
        with ui.spinner(f"Publishing {path.name}…"):
            stored = _storage().upload(path)
    ui.success(f"Published: {stored.filename}", emoji=ui.PUBLISH)
    print(stored.url)


def _cmd_library_download(filename: str, dest: str = "") -> None:
    dest_dir = Path(dest).expanduser() if dest else Path.cwd()
    dest_dir.mkdir(parents=True, exist_ok=True)
    # Backend chatter stays off stdout (see `ui.spinner`); the path below is
    # the command's only stdout line.
    with _storage_errors():
        with ui.spinner(f"Downloading {filename}…"):
            target = _storage().download(filename, dest_dir)
    ui.success(f"Downloaded: {filename}", emoji=ui.DOWNLOAD)
    print(target)


def _cmd_library_delete(filename: str, yes: bool = False) -> None:
    if not yes:
        if not sys.stdin.isatty():
            raise ui.fail(f"refusing to delete {filename} without --yes (stdin is not a terminal).")
        from rich.prompt import Confirm

        prompt = f"{ui.TRASH} Delete [accent]{filename}[/accent] from storage?"
        if not Confirm.ask(prompt, console=ui.err):
            ui.note("Aborted.")
            raise typer.Exit(1)
    with _storage_errors():
        _storage().delete(filename)
    ui.success(f"Deleted: {filename}", emoji=ui.TRASH)


# ── entry point ──────────────────────────────────────────────────────────────


def _print_overview() -> None:
    """Render the top-level help to stderr — used when a bare ``corpora`` runs.

    Typer's rich help writes straight to its own stdout console rather than
    into Click's formatter buffer, so the redirect — not the ``get_help``
    return value — is what lands it on stderr.
    """
    command = typer.main.get_command(app)
    # A bare Context skips the command's context_settings, which is where
    # the -h alias lives — pass them through so the overview matches --help.
    with ClickContext(command, info_name="corpora", **command.context_settings) as ctx:
        with contextlib.redirect_stdout(sys.stderr):
            command.get_help(ctx)


def main(argv: list[str] | None = None) -> int:
    # An unexpected exception is a bug report: render it with frames and
    # source rather than a bare Python traceback (`corpora_cli.ui`).
    ui.install_traceback()
    resolved = list(sys.argv[1:] if argv is None else argv)
    # Bare `corpora` prints the overview and exits 2 like any other bad
    # usage — Click would print a terse "Missing command" instead.
    if not resolved:
        _print_overview()
        return 2
    try:
        # standalone_mode=False keeps Click from calling sys.exit itself, so
        # the exit-code contract in this module's docstring stays in one
        # place. Click hands a command's `typer.Exit` back as the *return
        # value* in this mode (it only raises in standalone mode);
        # `ui.fail` usage errors (SystemExit 2) propagate untouched.
        result = app(args=resolved, prog_name="corpora", standalone_mode=False)
    except click_exceptions.UsageError as exc:
        # Click's own parse failures (unknown command, bad option, missing
        # argument), rendered through the CLI's palette.
        ui.error(exc.format_message())
        if exc.ctx is not None:
            ui.note(exc.ctx.get_usage())
            ui.note(f"Try '{exc.ctx.command_path} {exc.ctx.help_option_names[0]}' for help.")
        raise SystemExit(2) from exc
    except click_exceptions.Exit as exc:  # --help and completion exits
        return int(exc.exit_code or 0)
    except (KeyboardInterrupt, click_exceptions.Abort):
        # Ctrl-C is an answer, not a crash — no traceback for it.
        ui.log(f"{ui.WARN} interrupted.", style="warning")
        return 130
    finally:
        _release_command_resources()
    if result == 130:
        # Click swallows a KeyboardInterrupt inside a command and hands back
        # 130 instead of re-raising, so the interrupt note prints here.
        ui.log(f"{ui.WARN} interrupted.", style="warning")
        return 130
    return int(result) if isinstance(result, int) else 0


if __name__ == "__main__":
    raise SystemExit(main())
