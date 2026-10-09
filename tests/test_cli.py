"""Tests for the ``corpora`` terminal CLI (`corpora_cli.cli`).

The happy path runs the real pipeline end-to-end (plain text -> Text-Fabric
-> ``.cfm`` -> ``.corpus`` -> real `validate_corpus_archive`) -- the same
coverage philosophy as the converter tests, kept cheap with a tiny source.
Everything else (format inference, gate rejection, overwrite refusal, exit
codes) is exercised through `main()` with argv lists, never a subprocess.
The `library` commands are exercised against a fake storage backend
monkeypatched at the `make_corpus_storage` seam (the real backends are
covered by the storage service suite).
"""

import sys
import zipfile
from pathlib import Path

import pytest

from corpora_cli import cli


def _usage_error(excinfo, capsys) -> str:
    """Unpack a usage error: exit 2, message on stderr (`ui.fail`)."""
    assert excinfo.value.code == 2
    return capsys.readouterr().err


def _write_sample(path):
    path.write_text(
        "Sample Title\n\n"
        "Chapter 1\n\n"
        "Hello world this is a paragraph of test content for the cli.\n\n"
        "Another paragraph with more words to convert into an archive.\n"
    )
    return path


class TestConvert:
    def test_converts_plain_text_to_valid_corpus(self, tmp_path, capsys):
        source = _write_sample(tmp_path / "sample.txt")
        output = tmp_path / "out" / "sample.corpus"

        exit_code = cli.main(["convert", str(source), "-o", str(output)])

        assert exit_code == 0
        assert output.is_file()
        assert zipfile.is_zipfile(output)
        # The result path is the one thing printed to stdout (scripting
        # contract); logs go to stderr.
        captured = capsys.readouterr()
        assert captured.out.strip() == str(output)
        assert "Conversion complete." in captured.err
        # The source file is never consumed by the conversion.
        assert source.is_file()

        # And the built archive round-trips through `corpora validate`.
        assert cli.main(["validate", str(output)]) == 0
        assert "Validation: valid" in capsys.readouterr().err

    def test_default_output_is_slugified_title_in_cwd(self, tmp_path, monkeypatch, capsys):
        source = _write_sample(tmp_path / "My Great Book.txt")
        monkeypatch.chdir(tmp_path)

        assert cli.main(["convert", str(source)]) == 0

        out = capsys.readouterr().out.strip()
        # PlainTextParser derives the title from the filename stem.
        assert out.endswith("my-great-book.corpus")
        assert (tmp_path / "my-great-book.corpus").is_file()

    def test_refuses_to_overwrite_without_force(self, tmp_path, capsys):
        source = _write_sample(tmp_path / "sample.txt")
        output = tmp_path / "sample.corpus"
        output.write_bytes(b"existing")

        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(source), "-o", str(output)])
        assert "--force" in _usage_error(excinfo, capsys)
        assert output.read_bytes() == b"existing"

        assert cli.main(["convert", str(source), "-o", str(output), "--force"]) == 0
        assert zipfile.is_zipfile(output)

    def test_upload_gate_rejects_non_convertible_bytes(self, tmp_path, capsys):
        # PNG magic bytes with a .txt extension: format inference says
        # plain, the magic-byte gate (issue #173) says image -> exit 2.
        fake = tmp_path / "image.txt"
        fake.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)

        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(fake)])
        assert excinfo.value.code == 2
        assert "image" in capsys.readouterr().err

    def test_missing_source_errors(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(tmp_path / "nope.txt")])
        assert "not found" in _usage_error(excinfo, capsys)

    def test_zip_requires_explicit_format(self, tmp_path, capsys):
        archive = tmp_path / "dataset.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("member.txt", "content")

        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(archive)])
        assert "--format" in _usage_error(excinfo, capsys)

    def test_unknown_extension_requires_format(self, tmp_path, capsys):
        weird = tmp_path / "book.docx"
        weird.write_bytes(b"whatever")

        with pytest.raises(SystemExit) as excinfo:
            cli.main(["convert", str(weird)])
        assert "--format" in _usage_error(excinfo, capsys)

    def test_converter_error_is_user_facing_exit_1(self, tmp_path, capsys):
        # A ZIP that is not a Text-Fabric dataset: passes the byte gate
        # (it is a real zip) but the tf_zip converter raises its
        # user-facing ValueError -> ConversionError -> exit 1.
        archive = tmp_path / "dataset.zip"
        with zipfile.ZipFile(archive, "w") as zf:
            zf.writestr("readme.md", "not a TF dataset")

        exit_code = cli.main(["convert", str(archive), "--format", "tf_zip"])

        assert exit_code == 1
        assert "error:" in capsys.readouterr().err


class TestValidate:
    def test_invalid_archive_exits_1(self, tmp_path, capsys):
        bogus = tmp_path / "bogus.corpus"
        with zipfile.ZipFile(bogus, "w") as zf:
            zf.writestr("not-a-corpus.txt", "nope")

        assert cli.main(["validate", str(bogus)]) == 1
        assert "INVALID" in capsys.readouterr().err

    def test_missing_file_errors(self, tmp_path, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["validate", str(tmp_path / "nope.corpus")])
        assert "not found" in _usage_error(excinfo, capsys)

    def test_repeated_validation_does_not_leak_file_descriptors(self, tmp_path, capsys):
        """Context-Fabric mmap handles must be released at the CLI boundary."""
        fd_dir = Path("/dev/fd")
        if not fd_dir.is_dir():
            pytest.skip("this platform does not expose /dev/fd")

        source = _write_sample(tmp_path / "sample.txt")
        archive = tmp_path / "sample.corpus"
        assert cli.main(["convert", str(source), "-o", str(archive)]) == 0
        capsys.readouterr()

        def open_fd_count() -> int:
            return sum(1 for _ in fd_dir.iterdir())

        baseline = open_fd_count()
        for _ in range(4):
            assert cli.main(["validate", str(archive)]) == 0
            capsys.readouterr()

        # Keep a small allowance for test-runner/platform descriptors while
        # detecting the ~42 descriptors retained by each leaked CF load.
        assert open_fd_count() <= baseline + 2


class TestFormatInference:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("book.epub", "epub"),
            ("page.HTML", "html"),
            ("doc.xhtml", "html"),
            ("work.tei", "tei"),
            ("data.xml", "xml"),
            ("scan.pdf", "pdf"),
            ("notes.md", "plain"),
            ("plain.txt", "plain"),
        ],
    )
    def test_extension_mapping(self, tmp_path, filename, expected):
        source = tmp_path / filename
        source.write_text("x")
        assert cli._infer_format(source, None).value == expected

    def test_declared_format_wins(self, tmp_path):
        source = tmp_path / "dataset.zip"
        source.write_text("x")
        assert cli._infer_format(source, "tei_zip").value == "tei_zip"


class _FakeStored:
    def __init__(self, filename):
        self.filename = filename
        self.size_bytes = 2 * 1024 * 1024
        self.repo_id = "user/archives"
        self.url = f"https://hub/{filename}"


class _FakeStorage:
    def __init__(self):
        self.deleted: list[str] = []

    def list(self):
        return [_FakeStored("alpha.corpus"), _FakeStored("beta.corpus")]

    def download(self, filename, dest_dir):
        return Path(dest_dir) / filename

    def delete(self, filename):
        self.deleted.append(filename)

    def upload(self, local_path, filename=None):
        return _FakeStored(Path(local_path).name)


@pytest.fixture
def fake_storage(monkeypatch):
    storage = _FakeStorage()
    import admin.services.storage as storage_module

    monkeypatch.setattr(storage_module, "make_corpus_storage", lambda: storage)
    return storage


class TestLibrary:
    def test_list_renders_table_and_count(self, fake_storage, capsys):
        assert cli.main(["library", "list"]) == 0
        captured = capsys.readouterr()
        assert "alpha.corpus" in captured.out
        assert "2.0 MB" in captured.out
        assert "user/archives" in captured.out
        assert "2 stored corpora, 4.0 MB." in captured.err
        # The copyable follow-up hint rides stderr, never the data stream.
        assert "corpora library show alpha.corpus" in captured.err
        assert "corpora library show" not in captured.out

    def test_unconfigured_storage_exits_with_message(self, monkeypatch, capsys):
        import admin.services.storage as storage_module

        def raising():
            raise storage_module.StorageNotConfiguredError("set HF_STORAGE_REPO")

        monkeypatch.setattr(storage_module, "make_corpus_storage", raising)
        with pytest.raises(SystemExit) as excinfo:
            cli.main(["library", "list"])
        assert "storage not configured" in _usage_error(excinfo, capsys)


class TestLockedCommands:
    """`publish`/`download`/`delete` wait for `corpora auth` (issue #28)."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["library", "publish", "alpha.corpus"],
            ["library", "download", "alpha.corpus"],
            ["library", "delete", "alpha.corpus", "--yes"],
        ],
    )
    def test_locked_commands_refuse_and_never_reach_storage(self, argv, fake_storage, capsys):
        with pytest.raises(SystemExit) as excinfo:
            cli.main(argv)
        message = _usage_error(excinfo, capsys)
        assert "corpora auth" in message
        assert cli.AUTH_ISSUE in message
        assert fake_storage.deleted == []

    def test_locked_commands_stay_out_of_click_errors(self, capsys):
        # A typo must not advertise the commands the help hides.
        with pytest.raises(SystemExit):
            cli.main(["library", "bogus"])
        rendered = capsys.readouterr().err
        assert "No such command" in rendered
        for hidden in cli.LOCKED_LIBRARY_COMMANDS:
            assert hidden not in rendered

    def test_locked_commands_are_hidden_from_help(self, capsys):
        assert cli.main(["library", "--help"]) == 0
        rendered = capsys.readouterr().out
        assert "list" in rendered
        assert "show" in rendered
        for hidden in ("publish", "download", "delete"):
            assert hidden not in rendered


def _run(library_func, **fields):
    """Call a library implementation the way the command body will once
    unlocked: keyword arguments in, `typer.Exit` out for a failed run."""
    import typer

    try:
        library_func(**fields)
    except typer.Exit as exc:
        return exc.exit_code
    return 0


class TestLibraryImplementations:
    """The locked commands' innards, which the lock will hand back."""

    def test_publish_prints_url(self, fake_storage, tmp_path, capsys):
        corpus = tmp_path / "alpha.corpus"
        corpus.write_bytes(b"zip")
        assert _run(cli._cmd_library_publish, corpus=str(corpus)) == 0
        captured = capsys.readouterr()
        assert captured.out.strip() == "https://hub/alpha.corpus"
        assert "Published: alpha.corpus" in captured.err

    def test_publish_requires_existing_file(self, fake_storage, capsys):
        with pytest.raises(SystemExit) as excinfo:
            _run(cli._cmd_library_publish, corpus="/nope.corpus")
        assert "not found" in _usage_error(excinfo, capsys)

    def test_download_prints_destination(self, fake_storage, tmp_path, capsys):
        assert _run(cli._cmd_library_download, filename="alpha.corpus", dest=str(tmp_path)) == 0
        assert capsys.readouterr().out.strip() == str(tmp_path / "alpha.corpus")

    def test_delete_with_yes(self, fake_storage, capsys):
        assert _run(cli._cmd_library_delete, filename="alpha.corpus", yes=True) == 0
        assert fake_storage.deleted == ["alpha.corpus"]
        assert "Deleted: alpha.corpus" in capsys.readouterr().err

    def test_delete_without_yes_refuses_when_not_a_tty(self, fake_storage, capsys):
        # pytest's captured stdin is not a tty, so the guard must trip
        # before any prompt.
        with pytest.raises(SystemExit) as excinfo:
            _run(cli._cmd_library_delete, filename="alpha.corpus", yes=False)
        assert "--yes" in _usage_error(excinfo, capsys)
        assert fake_storage.deleted == []


class TestUnexpectedFailures:
    def test_main_installs_the_rich_traceback_handler(self):
        # Anything the CLI does not handle itself should reach the user as a
        # Rich traceback rather than the bare Python one.
        original = sys.excepthook
        try:
            cli.main([])
            assert sys.excepthook is not original
            assert sys.excepthook.__module__ == "rich.traceback"
        finally:
            sys.excepthook = original

    def test_ctrl_c_exits_130_without_a_traceback(self, tmp_path, monkeypatch, capsys):
        corpus = tmp_path / "sample.corpus"
        corpus.write_bytes(b"zip")

        def interrupt(_archive):
            raise KeyboardInterrupt

        monkeypatch.setattr(cli, "validate_archive", interrupt)

        assert cli.main(["validate", str(corpus)]) == 130
        captured = capsys.readouterr()
        assert "interrupted." in captured.err
        assert "Traceback" not in captured.err


class TestBareInvocation:
    def test_bare_corpora_prints_help(self, capsys):
        # Bare `corpora` prints the overview and exits 2 like any other
        # bad usage.
        assert cli.main([]) == 2
        assert "convert" in capsys.readouterr().err
