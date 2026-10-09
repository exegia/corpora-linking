"""Bounded citation recognition; catalog identity and passage resolution are external."""

import re
from collections.abc import Iterable

from pydantic import Field

from .locators import normalize_text
from .models import CitationLocator, Endpoint, NonEmpty, Provenance, Reference, TextLocator, Value


class BibleBook(Value):
    """Caller-curated aliases for a catalog work; no built-in versification authority."""

    work_id: NonEmpty
    aliases: tuple[NonEmpty, ...] = Field(min_length=1)


class BibleCitationDetector(Value):
    """Recognize alias chapter:verse[-verse] in an immutable, whole text stream.

    This initial grammar excludes lists, cross-chapter ranges and implicit books.
    Duplicate aliases produce separate unresolved hypotheses, never a chosen work.
    """

    books: tuple[BibleBook, ...] = Field(min_length=1)
    stream_id: NonEmpty
    profile: NonEmpty
    scheme_id: NonEmpty
    scheme_version: NonEmpty
    agent_id: NonEmpty

    def detect(self, source: Endpoint, text: str) -> Iterable[Reference]:
        if source.locators:
            raise ValueError("source must describe a whole stream, without selection locators")
        # Validate exact identity even when the input contains no citations.
        Endpoint.model_validate(
            {
                **source.model_dump(),
                "locators": [TextLocator(stream_id=self.stream_id, start=0, end=1, exact="x")],
            }
        )
        normalize_text(text)  # reject surrogates; never silently normalize the stream
        aliases = sorted(
            {a for book in self.books for a in book.aliases}, key=lambda a: (-len(a), a)
        )
        pattern = re.compile(
            r"(?<!\w)(?P<book>"
            + "|".join(re.escape(a) for a in aliases)
            + r")\s+(?P<chapter>[1-9][0-9]*):(?P<verse>[1-9][0-9]*)"
            + r"(?:[-–](?P<end>[1-9][0-9]*))?(?!\w)",
            re.IGNORECASE,
        )
        for match in pattern.finditer(text):
            # Reject partial recognition of unsupported compound addresses.
            if re.match(r"\s*[:,;–-]\s*\d", text[match.end() :]):
                continue
            if match["end"] and int(match["end"]) < int(match["verse"]):
                continue
            exact = match[0]
            selection = TextLocator(
                stream_id=self.stream_id,
                start=match.start(),
                end=match.end(),
                exact=exact,
                prefix=text[max(0, match.start() - 32) : match.start()],
                suffix=text[match.end() : match.end() + 32],
            )
            selected = Endpoint.model_validate({**source.model_dump(), "locators": [selection]})
            candidates = {
                book.work_id
                for book in self.books
                if any(alias.casefold() == match["book"].casefold() for alias in book.aliases)
            }
            for work_id in sorted(candidates):
                yield Reference(
                    source=selected,
                    target=Endpoint(
                        work_id=work_id,
                        locators=(
                            CitationLocator(
                                value=exact,
                                profile=self.profile,
                                scheme_id=self.scheme_id,
                                scheme_version=self.scheme_version,
                            ),
                        ),
                    ),
                    provenance=Provenance(
                        origin="automatic",
                        agent_id=self.agent_id,
                        method="bible-alias-chapter-verse/v1",
                        evidence=(
                            exact,
                            "ambiguous catalog alias"
                            if len(candidates) > 1
                            else "caller-supplied catalog alias",
                        ),
                    ),
                    resolution="unresolved",
                )
