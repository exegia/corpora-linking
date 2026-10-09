"""Zero-dependency parser and corpus model for Text-Fabric (.tf) files.

Deliberately stdlib-only, and deliberately independent of the corpora-py /
cfabric loaders: reconciliation must be able to read a corpus exactly as it
sits on disk — including one broken enough that the real loaders would choke —
and report *which line* is wrong rather than just "it broke".

Ported from the text-fabric-validator skill's ``tf_common.py``
(exegia/homebrew-corpora#41).
"""

from __future__ import annotations

import bisect
import os
import re
from dataclasses import dataclass, field

FEATURE_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
NODESPEC_RE = re.compile(r"^\d+(-\d+)?(,\d+(-\d+)?)*$")


def parse_spec(text: str):
    """Parse a TF node spec such as '5', '5-9' or '5-9,12'.

    Returns a list of inclusive (lo, hi) pairs, or None if `text` is not a
    node spec at all (which usually means the line is an implicit value).
    """
    if not NODESPEC_RE.match(text):
        return None
    out = []
    for part in text.split(","):
        if "-" in part:
            a, b = part.split("-", 1)
            out.append((int(a), int(b)))
        else:
            n = int(part)
            out.append((n, n))
    return out


@dataclass
class TFFile:
    """One parsed .tf file: header, metadata block and raw data lines."""

    path: str
    name: str  # feature name, from the filename stem
    kind: str = ""  # 'node' | 'edge' | 'config' | ''
    meta: dict = field(default_factory=dict)
    fmts: dict = field(default_factory=dict)  # '@fmt:x=y' entries
    data: list = field(default_factory=list)  # (lineno, text) after the blank line

    @property
    def value_type(self) -> str:
        return self.meta.get("valueType", "")


def parse_tf_file(path: str) -> TFFile:
    """Parse a .tf file leniently — reconciliation reads what is there."""
    name = os.path.basename(path)
    stem = name[:-3] if name.endswith(".tf") else name
    tf = TFFile(path=path, name=stem)

    with open(path, "rb") as fh:
        raw = fh.read()
    raw = raw.removeprefix(b"\xef\xbb\xbf").replace(b"\r\n", b"\n")
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return tf

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    if not lines:
        return tf

    header = lines[0].strip()
    if header in ("@node", "@edge", "@config"):
        tf.kind = header[1:]

    i = 1
    while i < len(lines) and lines[i].startswith("@"):
        entry = lines[i][1:]
        key, _, val = entry.partition("=")
        key = key.strip()
        if key.startswith("fmt:"):
            tf.fmts[key[4:]] = val
        else:
            tf.meta[key] = val
        i += 1

    if i < len(lines) and lines[i].strip() == "":
        i += 1

    for j in range(i, len(lines)):
        line = lines[j]
        if line == "" and j >= len(lines) - 1:
            continue
        tf.data.append((j + 1, line))
    return tf


@dataclass
class NodeTypeBlock:
    lo: int
    hi: int
    otype: str


class Corpus:
    """A .tf corpus directory: the WARP triad plus every feature file."""

    def __init__(self, directory: str):
        self.dir = directory
        self.files: dict[str, TFFile] = {}
        self.otype_blocks: list[NodeTypeBlock] = []
        self._block_starts: list[int] = []
        self.type_ranges: dict[str, list] = {}  # otype -> [(lo, hi)]
        self.slot_type = ""
        self.max_slot = 0
        self.max_node = 0
        self.oslots: dict[int, tuple] = {}  # node -> (lo, hi, n_slots)

    def scan(self):
        for entry in sorted(os.listdir(self.dir)):
            if not entry.endswith(".tf"):
                continue
            path = os.path.join(self.dir, entry)
            if not os.path.isfile(path):
                continue
            tf = parse_tf_file(path)
            if FEATURE_NAME_RE.match(tf.name):
                self.files[tf.name] = tf
        return self

    def build_otype(self):
        tf = self.files.get("otype")
        if tf is None:
            return
        cursor = 1
        for _, line in tf.data:
            fields = line.split("\t")
            if len(fields) >= 2 and parse_spec(fields[0]) is not None:
                spans = parse_spec(fields[0])
                value = fields[1]
            else:
                spans = [(cursor, cursor)]
                value = fields[0]
            for lo, hi in spans:
                if hi < lo:
                    continue
                self.otype_blocks.append(NodeTypeBlock(lo, hi, value))
                self.type_ranges.setdefault(value, []).append((lo, hi))
                cursor = hi + 1

        self.otype_blocks.sort(key=lambda b: b.lo)
        self._block_starts = [b.lo for b in self.otype_blocks]
        if self.otype_blocks:
            self.max_node = max(b.hi for b in self.otype_blocks)
            self.slot_type = self.otype_blocks[0].otype
            self.max_slot = 0
            for b in self.otype_blocks:
                if b.otype == self.slot_type and b.lo <= self.max_slot + 1:
                    self.max_slot = max(self.max_slot, b.hi)

    def otype_of(self, node: int) -> str:
        idx = bisect.bisect_right(self._block_starts, node) - 1
        if idx < 0:
            return ""
        b = self.otype_blocks[idx]
        return b.otype if b.lo <= node <= b.hi else ""

    def build_oslots(self):
        tf = self.files.get("oslots")
        if tf is None:
            return
        for _, line in tf.data:
            fields = line.split("\t")
            if len(fields) < 2:
                continue
            src_spec = parse_spec(fields[0])
            tgt_spec = parse_spec(fields[1])
            if src_spec is None or tgt_spec is None:
                continue
            slots = [(lo, hi) for lo, hi in tgt_spec if hi >= lo]
            if not slots:
                continue
            total = sum(hi - lo + 1 for lo, hi in slots)
            lo_all = min(lo for lo, _ in slots)
            hi_all = max(hi for _, hi in slots)
            for slo, shi in src_spec:
                for node in range(slo, shi + 1):
                    self.oslots[node] = (lo_all, hi_all, total)

    def load(self):
        self.scan()
        self.build_otype()
        self.build_oslots()
        return self

    # ---- convenience ---------------------------------------------------
    def otext(self):
        return self.files.get("otext")

    def section_config(self):
        """Return (sectionTypes, sectionFeatures) from otext.tf."""
        tf = self.otext()
        if tf is None:
            return [], []
        types = [t for t in tf.meta.get("sectionTypes", "").split(",") if t]
        feats = [f for f in tf.meta.get("sectionFeatures", "").split(",") if f]
        return types, feats

    def node_feature_values(self, feature: str):
        """Yield (node, value, lineno) for a node feature, expanding implicit numbering."""
        tf = self.files.get(feature)
        if tf is None or tf.kind == "config":
            return
        cursor = 1
        for lineno, line in tf.data:
            fields = line.split("\t")
            if len(fields) >= 2 and parse_spec(fields[0]) is not None:
                spans = parse_spec(fields[0])
                value = fields[1]
            else:
                spans = [(cursor, cursor)]
                value = fields[0]
            for lo, hi in spans:
                if hi < lo:
                    continue
                for n in range(lo, hi + 1):
                    yield n, value, lineno
                cursor = hi + 1

    def nodes_of_type(self, otype: str):
        for lo, hi in sorted(self.type_ranges.get(otype, [])):
            yield from range(lo, hi + 1)

    def span_of(self, node: int):
        """(first_slot, last_slot) for any node; slots map to themselves."""
        if node <= self.max_slot:
            return (node, node)
        rec = self.oslots.get(node)
        return (rec[0], rec[1]) if rec else None
