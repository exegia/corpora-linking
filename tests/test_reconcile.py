"""Tests for ``corpora schema`` and ``corpora reconcile`` (issue #41).

The happy path runs the real pipeline end-to-end (plain text -> `.corpus`
archive -> schema -> reconcile), the same philosophy as the convert tests.
The level-mapping semantics — pairwise comparison, inference gate, map
files, loud typos — are exercised against a tiny hand-built two-level
Text-Fabric corpus, including the regression case the issue demands: a
reference whose *parent* level is wrong, which deepest-only comparison
misses and pairwise comparison catches.
"""

import json
from pathlib import Path

import pytest

from corpora_cli import cli

TWOLEVEL_MD = """\
# Part One

alpha bravo charlie delta echo foxtrot golf hotel india juliett kilo lima

## Chapter 1

mike november oscar papa quebec romeo sierra tango uniform victor whiskey xray

## Chapter 2

yankee zulu apple banana cherry date elder fig grape honey iris jasmine

# Part Two

kiwi lemon mango nectar olive peach quince raspberry strawberry tangerine ugli vanilla

## Chapter 3

walnut xigua yam zucchini almond basil clove dill endive fennel ginger horseradish
"""

WORDS = TWOLEVEL_MD.replace("#", "").split()
WORDS = [w for w in WORDS if w.islower()]  # bodies only, 60 words


def _tf(header: str, meta: list[str], lines: list[str]) -> str:
    return "\n".join([header, *meta, "", *lines]) + "\n"


def _write_corpus(root: Path, part_spans: list[str]) -> Path:
    """A part/chapter corpus matching TWOLEVEL_MD; `part_spans` places the
    two part nodes (64, 65)."""
    root.mkdir(parents=True, exist_ok=True)
    files = {
        "otype.tf": _tf(
            "@node", ["@valueType=str"], ["1-60\tword", "61-63\tchapter", "64-65\tpart"]
        ),
        "oslots.tf": _tf(
            "@edge", ["@valueType=int"], ["61\t13-24", "62\t25-36", "63\t49-60", *part_spans]
        ),
        "otext.tf": _tf(
            "@config",
            [
                "@fmt:text-orig-full={word}{trailer}",
                "@sectionFeatures=part,chapter",
                "@sectionTypes=part,chapter",
            ],
            [],
        ),
        "word.tf": _tf("@node", ["@valueType=str"], WORDS),
        "trailer.tf": _tf("@node", ["@valueType=str"], [" "] * 60),
        "part.tf": _tf("@node", ["@valueType=str"], ["64\tPart One", "65\tPart Two"]),
        "chapter.tf": _tf(
            "@node", ["@valueType=str"], ["61\tChapter 1", "62\tChapter 2", "63\tChapter 3"]
        ),
    }
    for name, content in files.items():
        (root / name).write_text(content, encoding="utf-8")
    return root


@pytest.fixture
def two_level(tmp_path):
    """(schema_path, good_corpus, parentbad_corpus): chapters identical in
    both corpora; parentbad's part 2 starts one chapter early."""
    assert len(WORDS) == 60
    doc = tmp_path / "twolevel.md"
    doc.write_text(TWOLEVEL_MD, encoding="utf-8")
    schema = tmp_path / "twolevel.schema.json"
    assert cli.main(["schema", str(doc), "-o", str(schema)]) == 0
    good = _write_corpus(tmp_path / "good", ["64\t1-36", "65\t37-60"])
    parentbad = _write_corpus(tmp_path / "parentbad", ["64\t1-24", "65\t25-60"])
    return schema, good, parentbad


def _usage_error(excinfo, capsys) -> str:
    assert excinfo.value.code == 2
    return capsys.readouterr().err


class TestSchema:
    def test_extracts_markdown_levels_and_prints_path(self, tmp_path, capsys):
        doc = tmp_path / "twolevel.md"
        doc.write_text(TWOLEVEL_MD, encoding="utf-8")
        out = tmp_path / "s.json"

        assert cli.main(["schema", str(doc), "-o", str(out)]) == 0

        captured = capsys.readouterr()
        assert captured.out.strip() == str(out)
        schema = json.loads(out.read_text())
        assert schema["levels"] == ["h1", "h2"]
        assert schema["unit_counts"] == {"h1": 2, "h2": 3}
        # The alignment key: every unit carries its opening word shingle.
        assert schema["units"][0]["head"].startswith("alpha bravo charlie")

    def test_default_output_is_stem_in_cwd(self, tmp_path, monkeypatch, capsys):
        doc = tmp_path / "book.md"
        doc.write_text("# One\n\nhello world\n", encoding="utf-8")
        monkeypatch.chdir(tmp_path)

        assert cli.main(["schema", str(doc)]) == 0
        assert (tmp_path / "book.schema.json").is_file()

    def test_refuses_to_overwrite_without_force(self, tmp_path, capsys):
        doc = tmp_path / "book.md"
        doc.write_text("# One\n\nhello world\n", encoding="utf-8")
        out = tmp_path / "s.json"
        out.write_text("{}")

        with pytest.raises(SystemExit) as excinfo:
            cli.main(["schema", str(doc), "-o", str(out)])
        assert "--force" in _usage_error(excinfo, capsys)
        assert cli.main(["schema", str(doc), "-o", str(out), "--force"]) == 0

    def test_pdf_is_refused_with_guidance(self, tmp_path, capsys):
        pdf = tmp_path / "scan.pdf"
        pdf.write_bytes(b"%PDF-1.4 whatever")

        with pytest.raises(SystemExit) as excinfo:
            cli.main(["schema", str(pdf)])
        assert "Markdown" in _usage_error(excinfo, capsys)

    def test_missing_document_errors(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["schema", str(tmp_path / "nope.md")])
        assert "not found" in _usage_error(excinfo, capsys)


class TestReconcileEndToEnd:
    def test_converted_archive_reconciles_against_its_source(self, tmp_path, capsys):
        # The real pipeline: convert plain text, then confirm the archive
        # represents the document it came from — the .corpus is opened and
        # the tf directory located inside it.
        source = tmp_path / "sample.txt"
        source.write_text(
            "Sample Title\n\n"
            "Chapter 1\n\n"
            "Hello world this is a paragraph of test content for the cli.\n\n"
            "Another paragraph with more words to convert into an archive.\n"
        )
        corpus = tmp_path / "sample.corpus"
        schema = tmp_path / "sample.schema.json"
        assert cli.main(["convert", str(source), "-o", str(corpus)]) == 0
        assert cli.main(["schema", str(source), "-o", str(schema)]) == 0
        capsys.readouterr()

        exit_code = cli.main(["reconcile", str(corpus), "--schema", str(schema), "--yes"])

        assert exit_code == 0
        captured = capsys.readouterr()
        assert "**PASS**" in captured.out
        assert "Reconciliation: PASS" in captured.err


class TestLevelMapping:
    def test_explicit_map_compares_pairwise_clean(self, two_level, capsys):
        schema, good, _ = two_level
        exit_code = cli.main(
            [
                "reconcile",
                str(good),
                "--schema",
                str(schema),
                "--map",
                "h1=part",
                "--map",
                "h2=chapter",
            ]
        )
        assert exit_code == 0
        assert "**PASS**" in capsys.readouterr().out

    def test_pairwise_catches_parent_drift_deepest_only_misses_it(
        self, two_level, tmp_path, capsys
    ):
        # The issue's regression case: chapters are perfect, the parent
        # boundary is wrong. Single-level comparison stays green;
        # pairwise comparison fails with boundary drift at the part level.
        schema, _, parentbad = two_level
        deep_json = tmp_path / "deep.json"
        assert (
            cli.main(
                [
                    "reconcile",
                    str(parentbad),
                    "--schema",
                    str(schema),
                    "--ref-level",
                    "h2",
                    "--level",
                    "chapter",
                    "--quiet",
                    "--json",
                    str(deep_json),
                ]
            )
            == 0
        )
        # …but the blind spot is announced rather than silent.
        codes = [f["code"] for f in json.loads(deep_json.read_text())["findings"]]
        assert "RC007" in codes and "RC008" in codes

        pair_json = tmp_path / "pair.json"
        assert (
            cli.main(
                [
                    "reconcile",
                    str(parentbad),
                    "--schema",
                    str(schema),
                    "--map",
                    "h1=part",
                    "--map",
                    "h2=chapter",
                    "--quiet",
                    "--json",
                    str(pair_json),
                ]
            )
            == 1
        )
        codes = [f["code"] for f in json.loads(pair_json.read_text())["findings"]]
        assert "RC003" in codes

    def test_map_typo_fails_loudly_with_valid_options(self, two_level, capsys):
        schema, good, _ = two_level
        with pytest.raises(SystemExit) as excinfo:
            cli.main(
                [
                    "reconcile",
                    str(good),
                    "--schema",
                    str(schema),
                    "--map",
                    "h1=part",
                    "--map",
                    "h2=chaptr",
                ]
            )
        message = _usage_error(excinfo, capsys)
        assert "chaptr" in message
        assert "part, chapter" in message

    def test_inference_proposes_and_yes_accepts(self, two_level, tmp_path, capsys):
        schema, good, _ = two_level
        out = tmp_path / "inferred.json"
        exit_code = cli.main(
            [
                "reconcile",
                str(good),
                "--schema",
                str(schema),
                "--yes",
                "--quiet",
                "--json",
                str(out),
            ]
        )
        assert exit_code == 0
        captured = capsys.readouterr()
        assert "Inferred level mapping" in captured.err
        mapping = {row["ref"]: row["corpus"] for row in json.loads(out.read_text())["level_map"]}
        assert mapping == {"h1": "part", "h2": "chapter"}

    def test_unconfirmed_inference_refuses_when_not_a_tty(self, two_level, capsys):
        # pytest's captured stdin is not a tty, so without --yes the gate
        # must refuse rather than guess silently.
        schema, good, _ = two_level
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["reconcile", str(good), "--schema", str(schema)])
        assert "--yes" in _usage_error(excinfo, capsys)

    def test_map_file_round_trip(self, two_level, tmp_path, capsys):
        schema, good, _ = two_level
        map_file = tmp_path / "corpus.levelmap.json"
        assert (
            cli.main(
                [
                    "reconcile",
                    str(good),
                    "--schema",
                    str(schema),
                    "--map",
                    "h1=part",
                    "--map",
                    "h2=chapter",
                    "--map-file",
                    str(map_file),
                    "--quiet",
                ]
            )
            == 0
        )
        assert json.loads(map_file.read_text())["map"] == {"h1": "part", "h2": "chapter"}

        # A later run needs no flags and no prompt: the mapping is persisted.
        assert (
            cli.main(
                [
                    "reconcile",
                    str(good),
                    "--schema",
                    str(schema),
                    "--map-file",
                    str(map_file),
                    "--quiet",
                ]
            )
            == 0
        )

    def test_missing_corpus_and_schema_error(self, two_level, tmp_path, capsys):
        schema, good, _ = two_level
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["reconcile", str(tmp_path / "nope"), "--schema", str(schema)])
        assert "not found" in _usage_error(excinfo, capsys)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["reconcile", str(good), "--schema", str(tmp_path / "nope.json")])
        assert "corpora schema" in _usage_error(excinfo, capsys)
