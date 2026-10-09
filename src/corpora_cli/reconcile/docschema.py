"""Extract a canonical reference schema from a source document.

The output is one JSON shape regardless of source format, so the reconciler
only ever has to understand one thing:

    {
      "source": "mobydick.epub",
      "format": "epub",
      "title": "Moby-Dick; or, The Whale",
      "levels": ["part", "chapter"],
      "units": [
        {"id": "u0007", "level": "chapter", "ordinal": 1, "number": "1",
         "label": "Loomings", "parent": "u0001", "path": "chap001.xhtml",
         "word_count": 2314, "head": "call me ishmael some years ago",
         "tail": "and the great flood gates of the wonder world swung open"}
      ]
    }

`head` and `tail` are normalised word shingles used to align a unit against
slot ranges in a .tf corpus, which is far more robust than trusting titles.

Supported formats: epub, tei/xml (TEI or DocBook), markdown/plain text. A PDF
must be extracted to Markdown first (its text layer fails silently; the
extraction needs a quality gate before it can be trusted as a reference).

Ported from the text-fabric-validator skill's ``doc_schema.py``
(exegia/homebrew-corpora#41).
"""

from __future__ import annotations

import os
import re
import unicodedata
import zipfile
from xml.etree import ElementTree as ET

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
SHINGLE = 12


class SchemaError(ValueError):
    """A reference document that cannot be turned into a schema."""


# ----------------------------------------------------------------------
# helpers
# ----------------------------------------------------------------------


def norm_words(text: str):
    text = unicodedata.normalize("NFKC", text)
    return [w.lower() for w in WORD_RE.findall(text)]


def shingles(text: str):
    ws = norm_words(text)
    return " ".join(ws[:SHINGLE]), " ".join(ws[-SHINGLE:]), len(ws)


class HTMLText:
    """Minimal HTML/XHTML -> plain text, stdlib only."""

    BLOCK = re.compile(r"</(p|div|h[1-6]|li|tr|section|article|blockquote)>", re.I)
    SCRIPT = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.I | re.S)
    HEAD = re.compile(r"<head\b[^>]*>.*?</head>", re.I | re.S)
    TAG = re.compile(r"<[^>]+>")

    @classmethod
    def to_text(cls, html: str) -> str:
        html = cls.HEAD.sub(" ", html)
        html = cls.SCRIPT.sub(" ", html)
        html = cls.BLOCK.sub("\n", html)
        html = cls.TAG.sub(" ", html)
        html = (
            html.replace("&nbsp;", " ")
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
            .replace("&#39;", "'")
        )
        html = re.sub(r"&#x?[0-9A-Fa-f]+;", " ", html)
        return re.sub(r"[ \t]+", " ", html)


def local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag


def guess_number(label: str):
    """Pull a chapter/part number out of a heading if there is one."""
    if not label:
        return ""
    m = re.match(
        r"^\s*(?:chapter|chap\.?|part|book|canto|section|act|scene)\s+"
        r"([0-9]+|[IVXLCDM]+)\b",
        label,
        re.I,
    )
    if m:
        return m.group(1)
    m = re.match(r"^\s*([0-9]+)[.)\s]", label)
    return m.group(1) if m else ""


def make_unit(level, ordinal, label, text, path="", parent="", number=None):
    head, tail, wc = shingles(text)
    return {
        "id": f"u{ordinal:05d}",
        "level": level,
        "ordinal": ordinal,
        "number": number if number is not None else guess_number(label),
        "label": (label or "").strip(),
        "parent": parent,
        "path": path,
        "word_count": wc,
        "head": head,
        "tail": tail,
    }


# ----------------------------------------------------------------------
# EPUB
# ----------------------------------------------------------------------


def from_epub(path: str, level_names):
    zf = zipfile.ZipFile(path)
    container = ET.fromstring(zf.read("META-INF/container.xml"))
    opf_path = None
    for rf in container.iter():
        if local(rf.tag) == "rootfile":
            opf_path = rf.get("full-path")
            break
    if not opf_path:
        raise SchemaError("EPUB has no rootfile in META-INF/container.xml")
    base = os.path.dirname(opf_path)
    opf = ET.fromstring(zf.read(opf_path))

    title = ""
    manifest, spine, nav_href, ncx_href = {}, [], "", ""
    for el in opf.iter():
        t = local(el.tag)
        if t == "title" and not title:
            title = (el.text or "").strip()
        elif t == "item":
            iid, href = el.get("id"), el.get("href")
            manifest[iid] = href
            props = el.get("properties", "") or ""
            if "nav" in props.split():
                nav_href = href
            if (el.get("media-type") or "") == "application/x-dtbncx+xml":
                ncx_href = href
        elif t == "itemref":
            spine.append(el.get("idref"))

    def zread(rel):
        full = os.path.normpath(os.path.join(base, rel)).replace("\\", "/")
        try:
            return zf.read(full).decode("utf-8", "replace"), full
        except KeyError:
            return "", full

    # ---- table of contents (EPUB3 nav, else EPUB2 NCX) ----
    toc = []  # (depth, label, href)
    if nav_href:
        raw, _ = zread(nav_href)
        m = re.search(
            r"<nav[^>]*epub:type=[\"'][^\"']*toc[^\"']*[\"'][^>]*>(.*?)</nav>", raw, re.I | re.S
        )
        block = m.group(1) if m else raw
        depth = 0
        for token in re.finditer(r"<(/?)(ol|a)\b([^>]*)>(.*?)(?=<)", block, re.I | re.S):
            closing, tag, attrs, text = token.groups()
            if tag.lower() == "ol":
                depth += -1 if closing else 1
            elif tag.lower() == "a" and not closing:
                href = re.search(r'href=["\']([^"\']+)["\']', attrs)
                label = HTMLText.to_text(text).strip()
                if href:
                    toc.append((max(depth - 1, 0), label, href.group(1).split("#")[0]))
    elif ncx_href:
        raw, _ = zread(ncx_href)
        root = ET.fromstring(raw.encode("utf-8"))

        def walk(node, depth):
            for child in node:
                if local(child.tag) == "navPoint":
                    label, src = "", ""
                    for sub in child:
                        if local(sub.tag) == "navLabel":
                            for t in sub:
                                if local(t.tag) == "text":
                                    label = (t.text or "").strip()
                        elif local(sub.tag) == "content":
                            src = (sub.get("src") or "").split("#")[0]
                    if src:
                        toc.append((depth, label, src))
                    walk(child, depth + 1)

        for el in root:
            if local(el.tag) == "navMap":
                walk(el, 0)

    href_by_id = {iid: manifest.get(iid, "") for iid in spine}

    label_for = {}
    depth_for = {}
    for depth, label, href in toc:
        href = href.split("#")[0]
        label_for.setdefault(href, label)
        depth_for.setdefault(href, depth)

    max_depth = max(depth_for.values()) + 1 if depth_for else 1
    if level_names:
        levels = level_names
    else:
        levels = ["chapter"] if max_depth <= 1 else ["part", "chapter", "section"][:max_depth]
    while len(levels) < max_depth:
        levels.append(f"level{len(levels) + 1}")

    units, ordinal = [], 0
    parent_at = {}
    for iid in spine:
        href = href_by_id.get(iid) or ""
        if not href:
            continue
        raw, full = zread(href)
        if not raw:
            continue
        text = HTMLText.to_text(raw)
        if len(norm_words(text)) == 0:
            continue
        depth = depth_for.get(href, 0)
        depth = min(depth, len(levels) - 1)
        label = label_for.get(href) or _first_heading(raw) or os.path.basename(href)
        ordinal += 1
        parent = parent_at.get(depth - 1, "") if depth > 0 else ""
        unit = make_unit(levels[depth], ordinal, label, text, path=full, parent=parent)
        parent_at[depth] = unit["id"]
        units.append(unit)

    return {
        "source": os.path.basename(path),
        "format": "epub",
        "title": title,
        "levels": levels,
        "units": units,
    }


def _first_heading(html: str) -> str:
    m = re.search(r"<h([1-6])[^>]*>(.*?)</h\1>", html, re.I | re.S)
    return HTMLText.to_text(m.group(2)).strip() if m else ""


# ----------------------------------------------------------------------
# TEI / DocBook XML
# ----------------------------------------------------------------------

DIV_TAGS = {
    "div",
    "div0",
    "div1",
    "div2",
    "div3",
    "chapter",
    "section",
    "sect1",
    "sect2",
    "sect3",
    "part",
    "book",
}
HEAD_TAGS = {"head", "title"}


def from_xml(path: str, level_names):
    tree = ET.parse(path)
    root = tree.getroot()
    title = ""
    for el in root.iter():
        if local(el.tag) in HEAD_TAGS and (el.text or "").strip():
            title = el.text.strip()
            break

    units = []
    levels_seen = []
    counter = [0]

    def text_of(el):
        return " ".join(t for t in el.itertext())

    def head_of(el):
        for child in el:
            if local(child.tag) in HEAD_TAGS:
                return " ".join(t for t in child.itertext()).strip()
        return ""

    def walk(el, depth, parent_id):
        for child in el:
            tag = local(child.tag)
            if tag not in DIV_TAGS:
                walk(child, depth, parent_id)
                continue
            counter[0] += 1
            declared = child.get("type") or (tag if tag != "div" else "")
            level = declared or (
                level_names[depth]
                if level_names and depth < len(level_names)
                else f"div{depth + 1}"
            )
            if level not in levels_seen:
                levels_seen.append(level)
            unit = make_unit(
                level,
                counter[0],
                head_of(child),
                text_of(child),
                path=f"{tag}[{counter[0]}]",
                parent=parent_id,
                number=child.get("n") or None,
            )
            units.append(unit)
            walk(child, depth + 1, unit["id"])

    walk(root, 0, "")
    levels = level_names or levels_seen or ["div1"]
    return {
        "source": os.path.basename(path),
        "format": "xml",
        "title": title,
        "levels": levels,
        "units": units,
    }


# ----------------------------------------------------------------------
# Markdown / plain text
# ----------------------------------------------------------------------

ATX = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
SETEXT = re.compile(r"^(=+|-+)\s*$")


def from_markdown(path: str, level_names):
    with open(path, encoding="utf-8", errors="replace") as fh:
        lines = fh.read().split("\n")

    heads = []  # (line_index, depth, label)
    fence = False
    for idx, line in enumerate(lines):
        if line.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = ATX.match(line)
        if m:
            heads.append((idx, len(m.group(1)), m.group(2).strip()))
            continue
        if idx and SETEXT.match(line) and lines[idx - 1].strip():
            heads.append((idx - 1, 1 if line.strip()[0] == "=" else 2, lines[idx - 1].strip()))

    if not heads:
        text = "\n".join(lines)
        return {
            "source": os.path.basename(path),
            "format": "markdown",
            "title": "",
            "levels": ["document"],
            "units": [make_unit("document", 1, os.path.basename(path), text)],
        }

    depths = sorted({d for _, d, _ in heads})
    depth_rank = {d: i for i, d in enumerate(depths)}
    levels = level_names or [f"h{d}" for d in depths]
    while len(levels) < len(depths):
        levels.append(f"level{len(levels) + 1}")

    title = heads[0][2] if heads[0][1] == depths[0] else ""
    units, parent_at = [], {}
    for i, (idx, depth, label) in enumerate(heads):
        end = heads[i + 1][0] if i + 1 < len(heads) else len(lines)
        body = "\n".join(lines[idx + 1 : end])
        rank = depth_rank[depth]
        parent = parent_at.get(rank - 1, "") if rank else ""
        unit = make_unit(levels[rank], i + 1, label, body, path=f"line:{idx + 1}", parent=parent)
        parent_at[rank] = unit["id"]
        for deeper in [r for r in parent_at if r > rank]:
            parent_at.pop(deeper, None)
        units.append(unit)

    return {
        "source": os.path.basename(path),
        "format": "markdown",
        "title": title,
        "levels": levels,
        "units": units,
    }


def from_pdf(path: str, _level_names):
    raise SchemaError(
        f"'{os.path.basename(path)}' is a PDF — its text layer fails silently, so it "
        "cannot become a trusted reference directly. Extract it to Markdown first "
        "(with an OCR/quality gate, e.g. the text-fabric-validator skill's pdf_prep.py), "
        "then run this command on the .md file."
    )


# ----------------------------------------------------------------------


def detect_format(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    return {
        ".epub": "epub",
        ".xml": "xml",
        ".tei": "xml",
        ".dbk": "xml",
        ".md": "markdown",
        ".markdown": "markdown",
        ".txt": "markdown",
        ".pdf": "pdf",
    }.get(ext, "markdown")


def extract(path: str, fmt: str = "auto", levels=None):
    fmt = detect_format(path) if fmt == "auto" else fmt
    fn = {"epub": from_epub, "xml": from_xml, "markdown": from_markdown, "pdf": from_pdf}[fmt]
    schema = fn(path, levels or [])
    counts = {}
    for u in schema["units"]:
        counts[u["level"]] = counts.get(u["level"], 0) + 1
    schema["unit_counts"] = counts
    deepest = schema["levels"][-1]
    schema["total_words"] = sum(
        u["word_count"] for u in schema["units"] if u["level"] == deepest
    ) or sum(u["word_count"] for u in schema["units"])
    return schema
