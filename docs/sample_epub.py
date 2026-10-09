"""Build the sample EPUB the conversion GIF converts.

The demo book is generated rather than committed: an EPUB of any interesting
size is megabytes of binary in a repo that only wants it for one screen
recording, and a generated one keeps `make docs` self-contained (no network,
no fixture to keep in sync). The prose is written for this purpose, so
nothing in the recording is anyone else's text.

Usage::

    uv run python docs/sample_epub.py /tmp/corpora-docs/field-guide.epub
"""

from __future__ import annotations

import sys
from pathlib import Path

from ebooklib import epub

TITLE = "A Field Guide to Text Archives"
AUTHOR = "The corpora project"

PARAGRAPHS = [
    "A text archive earns its name the moment a reader can ask it a question "
    "and get the same answer twice. Everything else — the format, the "
    "compression, the folder layout — is bookkeeping in service of that one "
    "property.",
    "Structure is the part most collections lose first. A scanned page knows "
    "it is a page; it rarely knows it is the third page of the second chapter "
    "of a work that has four volumes. Recovering that hierarchy after the "
    "fact is slow, error-prone work.",
    "Tokenisation looks trivial until the corpus disagrees with you. "
    "Hyphenation across line breaks, editorial brackets, marginal glosses and "
    "footnote markers all arrive as characters and all mean something other "
    "than what they appear to be.",
    "A section reference is a promise: that the same string will resolve to "
    "the same passage next year, on someone else's machine, after the "
    "underlying files have been recompiled twice.",
    "Validation is cheaper than trust. A corpus that reports its own node "
    "counts, feature names and section levels can be checked in a second; one "
    "that does not has to be read end to end before anyone dares cite it.",
    "Portability is a property of the whole, not the parts. A directory of "
    "well-formed files that must be assembled in a particular order by a "
    "particular script is not portable; a single archive that carries its own "
    "manifest is.",
    "Every conversion is a series of small, reversible decisions about what "
    "counts as text. Writing those decisions down — in the archive, next to "
    "the data — is what separates a corpus from a pile of files.",
    "The reader you are building for is usually a program, and programs are "
    "unforgiving about whitespace, encodings and off-by-one section numbers "
    "in a way that human readers politely are not.",
]

CHAPTERS = [
    "What a Corpus Owes Its Reader",
    "Structure, and How It Is Lost",
    "Tokens Are Not Words",
    "References That Survive a Rebuild",
    "Checking the Archive Against Itself",
    "One File, No Assembly",
    "Writing Decisions Down",
    "Reading for Machines",
    "Editions, Variants and Other Trouble",
    "Metadata Nobody Regrets",
    "Sections Deeper Than Two",
    "The Long Tail of Encodings",
]

# Enough prose that the conversion takes long enough to watch.
PARAGRAPHS_PER_CHAPTER = 260


def build(path: Path) -> Path:
    book = epub.EpubBook()
    book.set_identifier("corpora-field-guide")
    book.set_title(TITLE)
    book.set_language("en")
    book.add_author(AUTHOR)

    spine: list[epub.EpubHtml] = []
    for number, heading in enumerate(CHAPTERS, start=1):
        body = "".join(
            f"<p>{PARAGRAPHS[(number + index) % len(PARAGRAPHS)]}</p>"
            for index in range(PARAGRAPHS_PER_CHAPTER)
        )
        chapter = epub.EpubHtml(title=heading, file_name=f"chapter-{number:02d}.xhtml", lang="en")
        chapter.content = f"<h1>{number}. {heading}</h1>{body}"
        book.add_item(chapter)
        spine.append(chapter)

    book.toc = tuple(spine)
    book.spine = ["nav", *spine]
    book.add_item(epub.EpubNcx())
    book.add_item(epub.EpubNav())

    path.parent.mkdir(parents=True, exist_ok=True)
    epub.write_epub(str(path), book)
    return path


if __name__ == "__main__":
    target = Path(sys.argv[1] if len(sys.argv) > 1 else "field-guide.epub")
    print(build(target))
