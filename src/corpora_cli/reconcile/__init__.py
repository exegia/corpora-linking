"""Reconcile a converted corpus against the source document it came from.

Stdlib-only (no corpora-py imports): the reconciler must read a corpus as it
sits on disk and stay usable on archives the heavy loaders cannot open. See
`engine` for the alignment machinery, `docschema` for reference extraction,
and `tfcorpus` for the lenient .tf reader they share.
"""

from corpora_cli.reconcile.engine import MappingError, Options, Result, format_evidence, run

__all__ = ["MappingError", "Options", "Result", "format_evidence", "run"]
