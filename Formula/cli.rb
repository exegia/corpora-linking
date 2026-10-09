# corpora — terminal conversion CLI, hosted in this repo (src/corpora_cli).
#
# The formula installs the corpora-cli package into its own virtualenv with
# pip resolving the (many) Python dependencies — corpora-py and everything
# under it — from PyPI, rather than vendoring every dependency as a Homebrew
# resource stanza: this is a personal tap, and the dependency closure
# (text-fabric, context-fabric, lxml, fastapi, ...) would be unmaintainable
# as resources.
#
# `url` points at this repo's own release-tag tarball; the tag's VERSION
# file is the package version (hatch reads it), so the tarball builds the
# exact released wheel (version is inferred from the tag URL — an explicit
# stanza fails `brew audit` as redundant). The bump workflow
# (.github/workflows/bump.yml) rewrites url/sha256 on each corpora-cli
# release tag. `corpora-api` and `cf-mcp` still come from the corpora-py
# dependency's entry points.
class Cli < Formula
  include Language::Python::Virtualenv

  desc "Convert documents into queryable .corpus text archives"
  homepage "https://github.com/exegia/homebrew-corpora"
  url "https://github.com/exegia/homebrew-corpora/archive/refs/tags/v1.2.1.tar.gz"
  sha256 "1ef7ce0586789a8dbedbdfb5f44675ccdd63567b217eb84d648c2a815f4828b8"
  license "MIT"

  depends_on "python@3.13"

  def install
    virtualenv_create(libexec, "python3.13")
    # Resolve corpora-py and the rest of the closure from PyPI; buildpath is
    # the corpora-cli source tree itself.
    system libexec/"bin/python", "-m", "pip", "install", buildpath.to_s
    # corpora-py < 2.3.0 also declares a `corpora` entry point; whichever
    # wheel pip happens to install last owns bin/corpora. Reinstall
    # corpora-cli on top so its script deterministically wins.
    system libexec/"bin/python", "-m", "pip", "install",
           "--force-reinstall", "--no-deps", buildpath.to_s
    # Strip the REPL/serving extras the CLI never reaches (uvicorn --reload
    # watchers, ipython completion) — the same set corpora-py's Vercel
    # deploy uninstalls as runtime-unreachable. Besides the weight,
    # Homebrew's post-install linkage fixer hard-fails on watchfiles'
    # Rust dylib ("Failed changing dylib ID"), so these must be gone
    # before Homebrew post-processes the keg.
    system libexec/"bin/python", "-m", "pip", "uninstall", "-y",
           "uvloop", "watchfiles", "jedi", "parso"
    bin.install_symlink libexec/"bin/corpora"
    bin.install_symlink libexec/"bin/corpora-api"
    bin.install_symlink libexec/"bin/cf-mcp"
  end

  # Homebrew's post-install processing leaves many of the pip-installed
  # native extensions (lxml, pdf-inspector, hf_xet, ...) with invalid ad-hoc
  # code signatures; on Apple Silicon the kernel then SIGKILLs the process
  # with "Code Signature Invalid" the moment such a page is executed. Re-sign
  # every Mach-O in the virtualenv after everything else has touched it —
  # post_install runs last (and on `brew postinstall corpora` for an
  # already-broken keg).
  # codesign is macOS-only; Linuxbrew has no codesign and no signature
  # enforcement, so skip re-signing there.
  def post_install
    return unless OS.mac?

    Dir[libexec/"lib/**/*.so", libexec/"lib/**/*.dylib"].each do |f|
      system "codesign", "--force", "--sign", "-", f
    end
  end

  test do
    (testpath/"sample.txt").write <<~TEXT
      Sample Title

      Chapter 1

      Hello world this is a paragraph of test content for the formula.

      Another paragraph with more words to convert into an archive.
    TEXT
    system bin/"corpora", "convert", "sample.txt", "-o", "sample.corpus"
    assert_path_exists testpath/"sample.corpus"
    system bin/"corpora", "validate", "sample.corpus"
  end
end
