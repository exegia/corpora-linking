"""Conservative text-anchor verification; never silently relocates an anchor."""

import unicodedata
from typing import Literal

from .models import TextLocator


def normalize_text(text: str, policy: Literal["preserve", "NFC"] = "preserve") -> str:
    if any(0xD800 <= ord(c) <= 0xDFFF for c in text):
        raise ValueError("text contains unpaired surrogates")
    if policy == "preserve":
        return text
    if policy == "NFC":
        return unicodedata.normalize("NFC", text)
    raise ValueError(f"unsupported normalization: {policy}")


def verify_text_anchor(
    locator: TextLocator, text: str, *, expected_revision: str, actual_revision: str
) -> Literal["valid", "stale"]:
    if expected_revision != actual_revision:
        return "stale"
    normalized = normalize_text(text, locator.normalization)
    if normalized[locator.start : locator.end] != locator.exact:
        return "stale"
    if locator.prefix and not normalized[: locator.start].endswith(locator.prefix):
        return "stale"
    if locator.suffix and not normalized[locator.end :].startswith(locator.suffix):
        return "stale"
    return "valid"
