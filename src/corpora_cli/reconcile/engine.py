"""Reconcile a .tf corpus against a reference document schema.

Answers the question "what did the conversion lose?" by aligning the units of
a reference document (`corpora_cli.reconcile.docschema`) with the section
nodes of a Text-Fabric corpus, then reporting missing, extra, misaligned and
mislabelled structure — and emitting *append-only* .tf patches that can be
reviewed before anything is overwritten.

Level mapping — reference levels (h1/h2, part/chapter, div1…) rarely share
names with the corpus's @sectionTypes, so the two sides are bridged first
(exegia/homebrew-corpora#41):

  1. explicit ``--map REF=CORPUS`` pairs, validated against both sides;
  2. a persisted map file (written back once confirmed);
  3. the ``--ref-level``/``--level`` single-pair escape hatch;
  4. otherwise the mapping is *inferred* from depth alignment, unit counts
     and anchor concordance, and must be confirmed by the caller.

Mapped levels are compared pairwise at every level, not just the deepest, so
part/book mismatches surface too. Levels left unmapped on either side are
reported (RC007/RC008) rather than silently dropped.

Alignment strategy inside a level pair, in order of trust:
  1. text anchors  — the reference unit's opening word shingle is located in
                     the corpus slot stream; robust to renumbering/retitling.
  2. label match   — normalised heading vs the section feature value.
  3. ordinal       — position in sequence, used only to break ties.

This module is UI-free: it raises `MappingError` for a bad request and calls
back into `confirm` for the inference gate; rendering belongs to the CLI.

Ported from the text-fabric-validator skill's ``tf_reconcile.py``.
"""

from __future__ import annotations

import difflib
import json
import os
import re
import unicodedata
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field

from corpora_cli.reconcile.tfcorpus import Corpus

WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)
SHINGLE = 12
INFER_ACCEPT = 0.35  # minimum pair score the inference will propose

# confirm(rows, unmapped_ref, unmapped_corpus) -> proceed?
Confirm = Callable[[list[dict], list[str], list[str]], bool]


class MappingError(ValueError):
    """A level mapping that cannot be resolved: bad syntax, a typo on either
    side, or an inferred mapping the caller did not confirm."""


@dataclass
class Options:
    """Everything `run` needs; mirrors the CLI flags one to one."""

    corpus_dir: str
    schema_path: str
    map_pairs: list[str] = field(default_factory=list)  # ["h1=book", ...]
    map_file: str = ""
    level: str = ""
    ref_level: str = ""
    label_feature: str = ""
    tolerance: int = 3
    report_path: str = ""
    json_path: str = ""
    patch_dir: str = ""


@dataclass
class Result:
    exit_code: int  # 0 clean, 1 error-severity findings
    report: str  # rendered Markdown
    findings: list[dict]
    mapping_rows: list[dict]
    mapping_source: str
    patch_manifest: dict | None


def norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", str(text)).lower()
    return " ".join(WORD_RE.findall(text))


def similarity(a: str, b: str) -> float:
    a, b = norm(a), norm(b)
    if not a or not b:
        return 0.0
    return difflib.SequenceMatcher(None, a, b).ratio()


# ----------------------------------------------------------------------
# Corpus side
# ----------------------------------------------------------------------


def corpus_units(corpus: Corpus, level: str, label_feature: str):
    """Return the corpus's own units at `level`, with slot spans and labels."""
    values = {}
    if label_feature and label_feature in corpus.files:
        for node, value, _ in corpus.node_feature_values(label_feature):
            values[node] = value
    units = []
    for node in corpus.nodes_of_type(level):
        span = corpus.span_of(node)
        if not span:
            units.append(
                {
                    "node": node,
                    "lo": None,
                    "hi": None,
                    "label": values.get(node, ""),
                    "word_count": 0,
                }
            )
            continue
        units.append(
            {
                "node": node,
                "lo": span[0],
                "hi": span[1],
                "label": values.get(node, ""),
                "word_count": span[1] - span[0] + 1,
            }
        )
    units.sort(key=lambda u: (u["lo"] is None, u["lo"] or 0, u["node"]))
    return units


def slot_stream(corpus: Corpus, text_features):
    """Normalised word per slot, 1-indexed (index 0 unused)."""
    stream = [""] * (corpus.max_slot + 1)
    for feat in text_features:
        if feat not in corpus.files:
            continue
        for node, value, _ in corpus.node_feature_values(feat):
            if 1 <= node <= corpus.max_slot and not stream[node]:
                w = norm(value)
                if w:
                    stream[node] = w.split()[0]
        if any(stream[1:]):
            break
    return stream


def pick_text_features(corpus: Corpus):
    """Guess which node features carry slot text, preferring the default format."""
    tf = corpus.otext()
    ordered = []
    if tf and tf.fmts:
        default = "text-orig-full" if "text-orig-full" in tf.fmts else next(iter(tf.fmts))
        for ref in re.findall(r"\{([^{}]+)\}", tf.fmts[default]):
            for alt in (a.strip() for a in ref.split("/")):
                if alt in corpus.files and alt not in ordered:
                    ordered.append(alt)
    for candidate in ("word", "text", "token", "form", "g_word_utf8"):
        if candidate in corpus.files and candidate not in ordered:
            ordered.append(candidate)
    return ordered


def find_anchor(stream, needle_words, search_lo, search_hi, min_hit=6):
    """Locate a word shingle in the slot stream. Returns the starting slot or None.

    The needle is letters-only, but the corpus may slot punctuation and
    numerals as their own (letterless, hence empty-normalised) slots — those
    are transparent to the match rather than breaking the run.
    """
    if not needle_words:
        return None
    n = min(len(needle_words), SHINGLE)
    probe = needle_words[:n]
    first = probe[0]
    best, best_score = None, 0
    for s in range(max(1, search_lo), min(len(stream), search_hi + 1)):
        if stream[s] != first:
            continue
        score, t = 0, s
        for word in probe:
            while t < len(stream) and not stream[t]:
                t += 1
            if t < len(stream) and stream[t] == word:
                score += 1
                t += 1
            else:
                break
        if score > best_score:
            best, best_score = s, score
            if score == n:
                break
    return best if best_score >= min(min_hit, n) else None


def anchor_units(units, stream, max_slot):
    """Anchor a level's reference units into the slot stream, scanning forward."""
    cursor = 1
    for u in units:
        u["_anchor"] = find_anchor(stream, u["head"].split(), cursor, max_slot)
        if u["_anchor"] is None:
            u["_anchor"] = find_anchor(stream, u["head"].split(), 1, max_slot)
        if u["_anchor"] is not None:
            cursor = u["_anchor"] + 1
    return units


def aggregate_word_counts(schema):
    """A parent unit's own word_count covers only its direct text (e.g. a part's
    intro before the first chapter). Fold descendants in so parent levels can be
    size-compared against their full corpus span."""
    kids = defaultdict(list)
    for u in schema["units"]:
        if u.get("parent"):
            kids[u["parent"]].append(u)

    def total(u):
        return u["word_count"] + sum(total(k) for k in kids[u["id"]])

    for u in schema["units"]:
        u["_agg_wc"] = total(u)


# ----------------------------------------------------------------------
# Level mapping
# ----------------------------------------------------------------------


def parse_map_pairs(map_pairs):
    """['h1=book', ...] -> ordered {ref: corpus}; raises MappingError on bad syntax."""
    mapping = {}
    for item in map_pairs:
        if item.count("=") != 1:
            raise MappingError(f"--map expects REF=CORPUS, got {item!r}")
        ref, cor = (part.strip() for part in item.split("="))
        if not ref or not cor:
            raise MappingError(f"--map expects REF=CORPUS, got {item!r}")
        if ref in mapping:
            raise MappingError(f"reference level {ref!r} mapped twice")
        mapping[ref] = cor
    return mapping


def validate_mapping(mapping, ref_levels, corpus_types):
    """Both sides of every pair must name a declared level; typos fail loudly."""
    errors = []
    for ref, cor in mapping.items():
        if ref not in ref_levels:
            errors.append(
                f"reference level {ref!r} is not in the schema's levels "
                f"({', '.join(ref_levels) or 'none'})"
            )
        if cor not in corpus_types:
            errors.append(
                f"corpus section type {cor!r} is not declared in @sectionTypes "
                f"({', '.join(corpus_types) or 'none'})"
            )
    targets = list(mapping.values())
    for cor in {t for t in targets if targets.count(t) > 1}:
        errors.append(f"corpus section type {cor!r} is the target of more than one mapping")
    if errors:
        raise MappingError("invalid level mapping:\n  " + "\n  ".join(errors))


def read_map_file(path):
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError) as exc:
        raise MappingError(f"cannot read map file {path}: {exc}") from exc
    mapping = data.get("map", data) if isinstance(data, dict) else None
    if not isinstance(mapping, dict) or not all(
        isinstance(k, str) and isinstance(v, str) for k, v in mapping.items()
    ):
        raise MappingError(f"{path} does not contain a {{ref: corpus}} level map")
    return mapping


def write_map_file(path, mapping, source, schema, corpus_types):
    payload = {
        "format": "corpora-levelmap/1",
        "map": mapping,
        "source": source,
        "schema_source": schema.get("source", ""),
        "schema_levels": schema.get("levels", []),
        "section_types": corpus_types,
    }
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def infer_mapping(corpus, schema, stream):
    """Propose {ref_level: corpus_type} from depth alignment, unit counts and
    anchor concordance. Returns (mapping, evidence_rows)."""
    ref_levels = schema["levels"]
    stypes, _ = corpus.section_config()
    spans_by_type = {
        t: [s for s in (corpus.span_of(n) for n in corpus.nodes_of_type(t)) if s] for t in stypes
    }
    units_by_level = {}
    for lvl in ref_levels:
        units = [u for u in schema["units"] if u["level"] == lvl]
        anchor_units(units, stream, corpus.max_slot)
        units_by_level[lvl] = units

    def concordance(units, spans):
        hits = tot = 0
        for u in units:
            if not u["head"]:
                continue  # nothing to locate; not evidence either way
            tot += 1
            a = u["_anchor"]
            if a is not None and any(lo <= a <= hi for lo, hi in spans):
                hits += 1
        return (hits / tot) if tot else None

    score = {}
    for i, ref in enumerate(ref_levels):
        for j, cor in enumerate(stypes):
            conc = concordance(units_by_level[ref], spans_by_type[cor])
            rcount, ccount = len(units_by_level[ref]), len(spans_by_type[cor])
            cratio = min(rcount, ccount) / max(rcount, ccount) if rcount and ccount else 0.0
            s = (0.5 if conc is None else conc) * (0.5 + 0.5 * cratio)
            # depth prior: innermost pairs with innermost, and so on outward
            if (len(ref_levels) - 1 - i) == (len(stypes) - 1 - j):
                s += 0.15
            score[(ref, cor)] = (s, conc, rcount, ccount)

    # Order-preserving matching (both lists are outermost-first) maximising
    # total score; a pair below INFER_ACCEPT is never proposed.
    n, m = len(ref_levels), len(stypes)
    memo: dict[tuple[int, int], tuple[float, list]] = {}

    def best(i, j):
        if i >= n or j >= m:
            return 0.0, []
        if (i, j) in memo:
            return memo[(i, j)]
        options = [best(i + 1, j), best(i, j + 1)]
        s = score[(ref_levels[i], stypes[j])][0]
        if s >= INFER_ACCEPT:
            sub, picks = best(i + 1, j + 1)
            options.append((s + sub, [(ref_levels[i], stypes[j]), *picks]))
        result = max(options, key=lambda o: o[0])
        memo[(i, j)] = result
        return result

    _, picks = best(0, 0)
    mapping = dict(picks)
    rows = []
    for ref, cor in picks:
        _, conc, rcount, ccount = score[(ref, cor)]
        rows.append(
            {
                "ref": ref,
                "corpus": cor,
                "ref_units": rcount,
                "corpus_nodes": ccount,
                "anchor_concordance": None if conc is None else round(conc, 2),
            }
        )
    return mapping, rows


def format_evidence(row):
    conc = row.get("anchor_concordance")
    conc_txt = "no text anchors" if conc is None else f"anchor concordance {conc:.2f}"
    return f"{row['ref_units']} unit(s) vs {row['corpus_nodes']} node(s), {conc_txt}"


def resolve_mapping(opts: Options, schema, corpus, stream, confirm: Confirm):
    """Work out the {ref_level: corpus_type} mapping from flags, file or inference.

    Returns (mapping_rows, source); raises MappingError when it cannot. The
    rows carry the evidence shown in the report and embedded in the JSON.
    """
    ref_levels = schema["levels"]
    stypes, _ = corpus.section_config()

    mapping, source = None, ""
    if opts.map_pairs:
        mapping = parse_map_pairs(opts.map_pairs)
        source = "explicit --map"
    elif opts.map_file and os.path.exists(opts.map_file):
        mapping = read_map_file(opts.map_file)
        source = f"map file {os.path.basename(opts.map_file)}"
    elif opts.ref_level or opts.level:
        level = opts.level or (stypes[-1] if stypes else "")
        ref_level = opts.ref_level or (ref_levels[-1] if ref_levels else "")
        if not level:
            raise MappingError("corpus declares no sectionTypes; pass --level or --map explicitly")
        if not ref_level:
            raise MappingError("schema declares no levels; pass --ref-level or --map explicitly")
        mapping = {ref_level: level}
        source = "--ref-level/--level flags"

    if mapping is not None:
        validate_mapping(mapping, ref_levels, stypes)
        rows = []
        for ref, cor in mapping.items():
            units = anchor_units(
                [u for u in schema["units"] if u["level"] == ref], stream, corpus.max_slot
            )
            spans = [s for s in (corpus.span_of(n) for n in corpus.nodes_of_type(cor)) if s]
            with_head = [u for u in units if u["head"]]
            hits = sum(
                1
                for u in with_head
                if u["_anchor"] is not None and any(lo <= u["_anchor"] <= hi for lo, hi in spans)
            )
            rows.append(
                {
                    "ref": ref,
                    "corpus": cor,
                    "ref_units": len(units),
                    "corpus_nodes": len(spans),
                    "anchor_concordance": round(hits / len(with_head), 2) if with_head else None,
                }
            )
        return rows, source

    if not stypes:
        raise MappingError("corpus declares no sectionTypes; pass --level or --map explicitly")
    mapping, rows = infer_mapping(corpus, schema, stream)
    if not mapping:
        raise MappingError(
            "could not infer a level mapping (no reference level scored "
            "against any corpus section type); pass --map explicitly"
        )
    unmapped_ref = [lv for lv in ref_levels if lv not in mapping]
    unmapped_cor = [t for t in stypes if t not in mapping.values()]
    if not confirm(rows, unmapped_ref, unmapped_cor):
        raise MappingError(
            "inferred level mapping was not confirmed — re-run with --yes, "
            "or pass --map / --map-file explicitly"
        )
    return rows, "inferred"


# ----------------------------------------------------------------------
# Alignment
# ----------------------------------------------------------------------


def align(corpus, schema, level, ref_level, label_feature, stream):
    cunits = corpus_units(corpus, level, label_feature)
    runits = anchor_units(
        [u for u in schema["units"] if u["level"] == ref_level], stream, corpus.max_slot
    )

    pairs, used = [], set()
    for u in runits:
        best, best_score, why = None, 0.0, ""
        for idx, c in enumerate(cunits):
            if idx in used or c["lo"] is None:
                continue
            score, reason = 0.0, []
            if u["_anchor"] is not None and c["lo"] <= u["_anchor"] <= c["hi"]:
                # anchor lands inside this corpus unit
                offset = u["_anchor"] - c["lo"]
                score += 0.6 if offset <= max(20, 0.05 * c["word_count"]) else 0.4
                reason.append(f"anchor@slot{u['_anchor']}")
            lab = similarity(u["label"], c["label"])
            if lab > 0.55:
                score += 0.3 * lab
                reason.append(f"label~{lab:.2f}")
            if u["number"] and norm(u["number"]) == norm(c["label"]):
                score += 0.3
                reason.append("number==label")
            if score > best_score:
                best, best_score, why = idx, score, ", ".join(reason)
        if best is not None and best_score >= 0.3:
            used.add(best)
            pairs.append(
                {"ref": u, "corpus": cunits[best], "score": round(best_score, 3), "evidence": why}
            )
        else:
            pairs.append(
                {
                    "ref": u,
                    "corpus": None,
                    "score": round(best_score, 3),
                    "evidence": why or "no anchor, no label match",
                }
            )

    orphan_corpus = [c for i, c in enumerate(cunits) if i not in used]
    return pairs, cunits, runits, orphan_corpus


# ----------------------------------------------------------------------
# Findings
# ----------------------------------------------------------------------


def analyse(pairs, orphan_corpus, level, ref_level, label_feature, tolerance):
    findings = []
    missing = [p for p in pairs if p["corpus"] is None]
    if missing:
        findings.append(
            {
                "code": "RC001",
                "severity": "error",
                "title": f"{len(missing)} reference {ref_level}(s) have no {level} node "
                f"in the corpus",
                "detail": [
                    f"#{p['ref']['ordinal']} {p['ref']['label']!r} "
                    f"({p['ref']['word_count']} words, anchor="
                    f"{p['ref']['_anchor']})"
                    for p in missing[:20]
                ],
                "why": "The text may still be present as slots, but there is no node to address it "
                "with, so it cannot be cited, retrieved by section, or walked as a unit.",
            }
        )
    if orphan_corpus:
        findings.append(
            {
                "code": "RC002",
                "severity": "warn",
                "title": f"{len(orphan_corpus)} corpus {level} node(s) match nothing in "
                f"the reference",
                "detail": [
                    f"node {c['node']} {c['label']!r} slots {c['lo']}-{c['hi']}"
                    for c in orphan_corpus[:20]
                ],
                "why": "Either the reference document omits this material (front/back matter "
                "is the usual culprit) or the converter invented a boundary that is not "
                "in the source.",
            }
        )

    drift, relabel, sizes = [], [], []
    for p in pairs:
        if not p["corpus"]:
            continue
        r, c = p["ref"], p["corpus"]
        if r["_anchor"] is not None and c["lo"] is not None:
            off = r["_anchor"] - c["lo"]
            if abs(off) > tolerance:
                drift.append((r, c, off))
        rwc = r.get("_agg_wc") or r["word_count"]
        if rwc and c["word_count"]:
            ratio = c["word_count"] / rwc
            if ratio < 0.85 or ratio > 1.18:
                sizes.append((r, c, rwc, ratio))
        if (
            r["label"]
            and c["label"]
            and similarity(r["label"], c["label"]) < 0.6
            and norm(r["number"]) != norm(c["label"])
        ):
            relabel.append((r, c))

    if drift:
        findings.append(
            {
                "code": "RC003",
                "severity": "error",
                "title": f"{len(drift)} {level} boundary/boundaries are offset from the reference",
                "detail": [
                    f"#{r['ordinal']} {r['label']!r}: corpus node {c['node']} starts at slot "
                    f"{c['lo']}, reference text starts at slot {r['_anchor']} "
                    f"(off by {off:+d})"
                    for r, c, off in drift[:20]
                ],
                "why": "An off-by-N boundary silently reassigns words to the neighbouring "
                "section, so queries scoped to a chapter return text from the one before "
                "or after.",
            }
        )
    if sizes:
        findings.append(
            {
                "code": "RC004",
                "severity": "warn",
                "title": f"{len(sizes)} {level}(s) differ in length from the reference by more "
                f"than ~15%",
                "detail": [
                    f"#{r['ordinal']} {r['label']!r}: corpus {c['word_count']} words vs "
                    f"reference {rwc} (ratio {ratio:.2f})"
                    for r, c, rwc, ratio in sizes[:20]
                ],
                "why": "Large ratios usually mean dropped footnotes, epigraphs or verse lines, or "
                "that tokenisation split/merged differently from the reference.",
            }
        )
    if relabel:
        findings.append(
            {
                "code": "RC005",
                "severity": "warn",
                "title": f"{len(relabel)} {level}(s) carry a {label_feature!r} value that does not "
                f"match the reference heading",
                "detail": [
                    f"node {c['node']}: corpus {c['label']!r} vs reference {r['label']!r}"
                    for r, c in relabel[:20]
                ],
                "why": "Section labels are the addressing scheme. A wrong label means the passage "
                "exists but cannot be found by the name a reader would use.",
            }
        )

    order_break = []
    prev = 0
    for p in pairs:
        if p["corpus"] and p["corpus"]["lo"] is not None:
            if p["corpus"]["lo"] < prev:
                order_break.append(p)
            prev = p["corpus"]["lo"]
    if order_break:
        findings.append(
            {
                "code": "RC006",
                "severity": "error",
                "title": f"{len(order_break)} {level}(s) appear in a different order than the "
                f"reference",
                "detail": [
                    f"#{p['ref']['ordinal']} {p['ref']['label']!r} -> node "
                    f"{p['corpus']['node']} at slot {p['corpus']['lo']}"
                    for p in order_break[:20]
                ],
                "why": "Reading order comes from slot order. Reordered sections mean the corpus "
                "tells the story in the wrong sequence.",
            }
        )
    return findings


def unmapped_findings(unmapped_ref, unmapped_cor, schema, corpus):
    findings = []
    if unmapped_ref:
        counts = schema.get("unit_counts", {})
        findings.append(
            {
                "code": "RC007",
                "severity": "warn",
                "title": f"{len(unmapped_ref)} reference level(s) have no corpus mapping and were "
                f"not compared",
                "detail": [
                    f"{lvl!r} — {counts.get(lvl, '?')} unit(s) in the reference"
                    for lvl in unmapped_ref
                ],
                "why": "The source document has structure the corpus does not model (or the "
                "mapping is incomplete). Whatever happens at this level — missing parts, drifted "
                "boundaries — goes unchecked until it is mapped.",
            }
        )
    if unmapped_cor:
        findings.append(
            {
                "code": "RC008",
                "severity": "warn",
                "title": f"{len(unmapped_cor)} corpus section type(s) have no reference "
                f"mapping and were not compared",
                "detail": [
                    f"{t!r} — {sum(1 for _ in corpus.nodes_of_type(t))} node(s) in the corpus"
                    for t in unmapped_cor
                ],
                "why": "The corpus models structure the reference does not (or the mapping is "
                "incomplete). These nodes cannot be verified against the source at all.",
            }
        )
    return findings


# ----------------------------------------------------------------------
# Patch generation (append-only)
# ----------------------------------------------------------------------


def build_patches(corpus, level_jobs, patch_dir):
    """Propose append-only additions for reference units the corpus lacks.

    `level_jobs` is a list of {"level", "ref_level", "label_feature", "pairs"},
    outermost level first. New nodes are numbered from maxNode+1, one
    contiguous block per level.

    Append-only matters: inserting nodes in the middle would renumber every
    node above the insertion point and invalidate every other .tf file plus
    any published node IDs. Adding a fresh block above maxNode touches only
    otype.tf, oslots.tf and the label feature(s).
    """
    unanchored = []
    per_level = []
    for job in level_jobs:
        missing = [p["ref"] for p in job["pairs"] if p["corpus"] is None and p["ref"]["_anchor"]]
        unanchored += [
            p["ref"] for p in job["pairs"] if p["corpus"] is None and not p["ref"]["_anchor"]
        ]
        if missing:
            per_level.append((job, missing))
    if not per_level:
        return None, unanchored

    os.makedirs(patch_dir, exist_ok=True)
    next_node = corpus.max_node + 1
    assignments, blocks = [], []
    for job, missing in per_level:
        # Give each missing unit a slot span: from its anchor to just before
        # the next anchored unit at the same level (or the end of the corpus).
        anchors = sorted({p["ref"]["_anchor"] for p in job["pairs"] if p["ref"]["_anchor"]})
        start = next_node
        for ref in sorted(missing, key=lambda r: r["_anchor"]):
            a = ref["_anchor"]
            later = [x for x in anchors if x > a]
            end = (later[0] - 1) if later else corpus.max_slot
            wc = ref.get("_agg_wc") or ref["word_count"]
            end = max(a, min(end, a + max(wc * 2, wc + 50)))
            assignments.append(
                {
                    "node": next_node,
                    "ref": ref,
                    "lo": a,
                    "hi": end,
                    "level": job["level"],
                    "label_feature": job["label_feature"],
                }
            )
            next_node += 1
        blocks.append({"type": job["level"], "range": [start, next_node - 1]})

    # otype.tf — original content plus one new contiguous block per level.
    otype_src = corpus.files["otype"]
    with open(os.path.join(patch_dir, "otype.tf"), "w", encoding="utf-8") as fh:
        fh.write("@node\n")
        for k, v in otype_src.meta.items():
            fh.write(f"@{k}={v}\n" if v != "" else f"@{k}\n")
        fh.write("\n")
        for _, line in otype_src.data:
            fh.write(line + "\n")
        for b in blocks:
            lo, hi = b["range"]
            fh.write(f"{lo}-{hi}\t{b['type']}\n" if hi > lo else f"{lo}\t{b['type']}\n")

    oslots_src = corpus.files["oslots"]
    with open(os.path.join(patch_dir, "oslots.tf"), "w", encoding="utf-8") as fh:
        fh.write("@edge\n")
        for k, v in oslots_src.meta.items():
            fh.write(f"@{k}={v}\n" if v != "" else f"@{k}\n")
        fh.write("\n")
        for _, line in oslots_src.data:
            fh.write(line + "\n")
        for a in assignments:
            fh.write(f"{a['node']}\t{a['lo']}-{a['hi']}\n")

    label_files = []
    by_feature = defaultdict(list)
    for a in assignments:
        if a["label_feature"]:
            by_feature[a["label_feature"]].append(a)
    for feature, feature_assignments in by_feature.items():
        src = corpus.files.get(feature)
        label_files.append(f"{feature}.tf")
        with open(os.path.join(patch_dir, f"{feature}.tf"), "w", encoding="utf-8") as fh:
            fh.write("@node\n")
            if src:
                for k, v in src.meta.items():
                    fh.write(f"@{k}={v}\n" if v != "" else f"@{k}\n")
            else:
                fh.write("@valueType=str\n")
            fh.write("\n")
            if src:
                for _, line in src.data:
                    fh.write(line + "\n")
            for a in feature_assignments:
                label = a["ref"]["number"] or a["ref"]["label"]
                fh.write(f"{a['node']}\t{label}\n")

    manifest = {
        "strategy": "append-only",
        "new_node_blocks": blocks,
        "files_rewritten": ["otype.tf", "oslots.tf"] + label_files,
        "additions": [
            {
                "node": a["node"],
                "type": a["level"],
                "label": a["ref"]["label"],
                "number": a["ref"]["number"],
                "slots": [a["lo"], a["hi"]],
                "reference_ordinal": a["ref"]["ordinal"],
                "reference_word_count": a["ref"]["word_count"],
            }
            for a in assignments
        ],
        "unanchored": [
            {"label": r["label"], "ordinal": r["ordinal"], "word_count": r["word_count"]}
            for r in unanchored
        ],
        "caveats": [
            "Node IDs above maxNode are appended, so no existing node is renumbered and every "
            "other .tf file stays valid.",
            "Slot spans are inferred from text anchors; check the boundaries in the report "
            "before applying.",
            "If the reference contains text the corpus has no slots for, appending nodes cannot "
            "fix it — that requires re-running the conversion over the source.",
            "Appending creates a second, non-adjacent block for each patched node type. That is "
            "the deliberate price of not renumbering; if a single contiguous block matters more "
            "than stable node IDs, re-convert the source instead of applying this patch.",
        ],
    }
    with open(os.path.join(patch_dir, "MANIFEST.json"), "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2, ensure_ascii=False)
    return manifest, unanchored


# ----------------------------------------------------------------------
# Report
# ----------------------------------------------------------------------


def build_report(
    corpus,
    schema,
    mapping_rows,
    mapping_source,
    unmapped_ref,
    unmapped_cor,
    pair_results,
    findings,
    manifest,
    unanchored,
):
    lines = [
        f"# Reconciliation — `{os.path.basename(os.path.abspath(corpus.dir))}` vs "
        f"`{schema['source']}`",
        "",
    ]
    errors = sum(1 for f in findings if f["severity"] == "error")
    lines.append(
        "**FAIL** — the corpus does not represent the reference document."
        if errors
        else "**PASS** — structure matches the reference within tolerance."
    )
    lines.append("")
    lines.append(f"## Level mapping ({mapping_source})")
    lines.append("")
    lines.append("| reference | corpus | evidence |")
    lines.append("|---|---|---|")
    for row in mapping_rows:
        lines.append(f"| `{row['ref']}` | `{row['corpus']}` | {format_evidence(row)} |")
    for lvl in unmapped_ref:
        lines.append(f"| `{lvl}` | — | unmapped (RC007) |")
    for t in unmapped_cor:
        lines.append(f"| — | `{t}` | unmapped (RC008) |")
    lines.append("")
    lines.append("## Alignment")
    lines.append("")
    lines.append(f"Reference: `{schema['source']}` ({schema['format']})")
    lines.append("")
    lines.append("| reference level | corpus level | matched |")
    lines.append("|---|---|---|")
    for res in pair_results:
        matched = sum(1 for p in res["pairs"] if p["corpus"])
        lines.append(
            f"| `{res['ref_level']}` — {len(res['pairs'])} unit(s) "
            f"| `{res['level']}` — {len(res['cunits'])} node(s) "
            f"| {matched}/{len(res['pairs'])} |"
        )
    lines.append("")
    lines.append(f"## Findings ({len(findings)})")
    lines.append("")
    if not findings:
        lines.append("No discrepancies found.")
    for f in findings:
        lines.append(f"### {f['code']} · {f['severity'].upper()} — {f['title']}")
        lines.append("")
        for d in f["detail"]:
            lines.append(f"- {d}")
        if len(f["detail"]) >= 20:
            lines.append("- …truncated; see the JSON output for the full list.")
        lines.append("")
        lines.append(f"*Why it matters:* {f['why']}")
        lines.append("")

    lines.append("## Suggested updates")
    lines.append("")
    if manifest:
        block_txt = ", ".join(
            f"`{b['type']}` **{b['range'][0]}–{b['range'][1]}**"
            for b in manifest["new_node_blocks"]
        )
        lines.append(
            f"Append-only patch: {len(manifest['additions'])} new node(s) as block(s) "
            f"{block_txt} (above the current maxNode, so nothing is renumbered)."
        )
        lines.append("")
        lines.append(
            "Rewritten files: " + ", ".join("`" + f + "`" for f in manifest["files_rewritten"])
        )
        lines.append("")
        lines.append("| new node | type | label | slots | ref words |")
        lines.append("|---|---|---|---|---|")
        for a in manifest["additions"][:40]:
            lines.append(
                f"| {a['node']} | {a['type']} | {a['label'] or a['number']} | "
                f"{a['slots'][0]}–{a['slots'][1]} | {a['reference_word_count']} |"
            )
        lines.append("")
        for c in manifest["caveats"]:
            lines.append(f"- {c}")
        lines.append("")
    else:
        lines.append("No append-only patch generated.")
        lines.append("")
    if unanchored:
        lines.append("### Cannot be patched by appending nodes")
        lines.append("")
        for r in unanchored[:20]:
            lines.append(
                f"- #{r['ordinal']} {r['label']!r} ({r['word_count']} reference words) "
                f"— its opening text was not found anywhere in the corpus slot stream."
            )
        lines.append("")
        lines.append(
            "*These units are missing at the **slot** level: the words themselves are "
            "absent. Adding nodes cannot recover them — re-run the conversion over the "
            "source document so the slots are created, then re-validate.*"
        )
        lines.append("")
    return "\n".join(lines) + "\n"


# ----------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------


def run(opts: Options, confirm: Confirm) -> Result:
    """Reconcile and write any requested artefacts. Raises MappingError for a
    bad or unconfirmed mapping; the caller owns rendering and exit codes."""
    corpus = Corpus(opts.corpus_dir).load()
    with open(opts.schema_path, encoding="utf-8") as fh:
        schema = json.load(fh)
    aggregate_word_counts(schema)
    stream = slot_stream(corpus, pick_text_features(corpus))

    mapping_rows, mapping_source = resolve_mapping(opts, schema, corpus, stream, confirm)
    mapping = {row["ref"]: row["corpus"] for row in mapping_rows}

    stypes, sfeats = corpus.section_config()
    if opts.map_file:
        write_map_file(opts.map_file, mapping, mapping_source, schema, stypes)

    ref_levels = schema["levels"]
    unmapped_ref = [lv for lv in ref_levels if lv not in mapping]
    unmapped_cor = [t for t in stypes if t not in mapping.values()]

    def feature_for(level):
        if opts.label_feature and len(mapping) == 1:
            return opts.label_feature
        if level in stypes and len(sfeats) == len(stypes):
            return sfeats[stypes.index(level)]
        return level

    # Compare pairwise at every mapped level, outermost first.
    ordered = sorted(
        mapping.items(), key=lambda kv: ref_levels.index(kv[0]) if kv[0] in ref_levels else 99
    )
    pair_results, findings = [], []
    for ref_level, level in ordered:
        label_feature = feature_for(level)
        pairs, cunits, _, orphans = align(corpus, schema, level, ref_level, label_feature, stream)
        pair_results.append(
            {
                "ref_level": ref_level,
                "level": level,
                "label_feature": label_feature,
                "pairs": pairs,
                "cunits": cunits,
            }
        )
        findings += analyse(pairs, orphans, level, ref_level, label_feature, opts.tolerance)
    findings += unmapped_findings(unmapped_ref, unmapped_cor, schema, corpus)

    manifest, unanchored = (None, [])
    if opts.patch_dir:
        manifest, unanchored = build_patches(corpus, pair_results, opts.patch_dir)
    else:
        unanchored = [
            p["ref"]
            for res in pair_results
            for p in res["pairs"]
            if p["corpus"] is None and not p["ref"]["_anchor"]
        ]

    report = build_report(
        corpus,
        schema,
        mapping_rows,
        mapping_source,
        unmapped_ref,
        unmapped_cor,
        pair_results,
        findings,
        manifest,
        unanchored,
    )
    if opts.report_path:
        with open(opts.report_path, "w", encoding="utf-8") as fh:
            fh.write(report)

    if opts.json_path:
        deepest = pair_results[-1] if pair_results else None
        payload = {
            "corpus_dir": os.path.abspath(opts.corpus_dir),
            "schema": opts.schema_path,
            "level_map": mapping_rows,
            "level_map_source": mapping_source,
            "unmapped_reference_levels": unmapped_ref,
            "unmapped_corpus_types": unmapped_cor,
            "corpus_level": deepest["level"] if deepest else "",
            "reference_level": deepest["ref_level"] if deepest else "",
            "findings": findings,
            "levels": [
                {
                    "reference_level": res["ref_level"],
                    "corpus_level": res["level"],
                    "label_feature": res["label_feature"],
                    "matched": sum(1 for p in res["pairs"] if p["corpus"]),
                    "reference_units": len(res["pairs"]),
                    "pairs": [
                        {
                            "reference": {
                                k: v for k, v in p["ref"].items() if not k.startswith("_")
                            }
                            | {"anchor": p["ref"]["_anchor"]},
                            "corpus": p["corpus"],
                            "score": p["score"],
                            "evidence": p["evidence"],
                        }
                        for p in res["pairs"]
                    ],
                }
                for res in pair_results
            ],
            "patch_manifest": manifest,
        }
        with open(opts.json_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)

    exit_code = 1 if any(f["severity"] == "error" for f in findings) else 0
    return Result(
        exit_code=exit_code,
        report=report,
        findings=findings,
        mapping_rows=mapping_rows,
        mapping_source=mapping_source,
        patch_manifest=manifest,
    )
