# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Locate and splice Y.Text fields inside a BlockSuite doc.

CRITICAL — yrs/pycrdt `Text` offsets are UTF-8 BYTE offsets, not Python string
indices. Handing pycrdt a codepoint index silently corrupts any text containing
multi-byte characters (em dashes, curly quotes, accents) and panics the Rust
layer on astral characters (emoji). Every index that crosses into pycrdt MUST go
through `byte_offset()`. This module is the only place that does that conversion.

A "text field" is one editable Y.Text on a block: `prop:text` on paragraphs,
lists and code blocks, and `prop:cells.<row>:<col>.text` on tables — which is why
in-place replacement reaches inside table cells that a Markdown rewrite could not
reproduce. `prop:title` is deliberately excluded: the page title is mirrored in
the workspace root doc's meta, so it is edited only via `set_title`.
"""
import re

from pycrdt import Text

TITLE_KEY = "prop:title"


class TextField:
    """One editable Y.Text on a block, plus its plain-text value at load time."""

    __slots__ = ("block_id", "key", "flavour", "text", "value")

    def __init__(self, block_id, key, flavour, text, value):
        self.block_id = block_id
        self.key = key
        self.flavour = flavour
        self.text = text
        self.value = value

    @property
    def ref(self):
        return f"{self.block_id}/{self.key}"

    def __repr__(self):
        return f"TextField({self.ref}, {self.flavour}, {self.value[:40]!r})"


class Match:
    """An occurrence of a needle inside one text field, at a Python string index."""

    __slots__ = ("field", "index", "length")

    def __init__(self, field, index, length):
        self.field = field
        self.index = index
        self.length = length


def byte_offset(s, i):
    """UTF-8 byte offset of Python string index `i` — the only unit pycrdt accepts."""
    return len(s[:i].encode("utf-8"))


def _keys(block):
    try:
        return list(block.keys())
    except Exception:
        return []


def _get(block, key, default=None):
    try:
        return block[key]
    except Exception:
        return default


def text_fields(blocks):
    """Every editable Y.Text in the doc, in stable (block id, key) order."""
    out = []
    try:
        block_ids = sorted(blocks.keys())
    except Exception:
        return out
    for bid in block_ids:
        block = _get(blocks, bid)
        if block is None:
            continue
        flavour = _get(block, "sys:flavour", "?")
        for key in sorted(_keys(block)):
            if not key.startswith("prop:") or key == TITLE_KEY:
                continue
            val = _get(block, key)
            if isinstance(val, Text):
                out.append(TextField(bid, key, flavour, val, str(val)))
    return out


def find_matches(fields, needle):
    """Every non-overlapping occurrence of `needle`, ascending by field then index."""
    if not needle:
        raise ValueError("search text must not be empty")
    matches = []
    for field in fields:
        start = 0
        while True:
            i = field.value.find(needle, start)
            if i < 0:
                break
            matches.append(Match(field, i, len(needle)))
            start = i + len(needle)
    return matches


def apply_match(match, replacement):
    """Splice one match. Caller MUST already be inside doc.transaction().

    Offsets are derived from the field's load-time value, so a caller replacing
    several matches in one field must apply them in descending index order.
    """
    value, text = match.field.value, match.field.text
    start = byte_offset(value, match.index)
    end = byte_offset(value, match.index + match.length)
    del text[start:end]
    if replacement:
        text.insert(start, replacement)


def expected_values(matches, replacement):
    """{field ref: value the field must hold after all its matches are applied}.

    Computed with plain Python string surgery so validation is independent of the
    CRDT path that produced the change.
    """
    by_ref = {}
    for m in matches:
        by_ref.setdefault(m.field.ref, []).append(m)
    out = {}
    for ref, ms in by_ref.items():
        value = ms[0].field.value
        for m in sorted(ms, key=lambda x: x.index, reverse=True):
            value = value[: m.index] + replacement + value[m.index + m.length :]
        out[ref] = value
    return out


def current_values(blocks):
    """{field ref: plain value} for every text field — used to prove nothing else moved."""
    return {f.ref: f.value for f in text_fields(blocks)}


def context(value, index, length, replacement, width=42):
    """A one-line before/after preview centred on a match, for --dry-run output."""
    lo = max(0, index - width)
    hi = min(len(value), index + length + width)
    lead = "…" if lo > 0 else ""
    tail = "…" if hi < len(value) else ""
    after = value[:index] + replacement + value[index + length :]
    a_hi = min(len(after), index + len(replacement) + width)
    return (
        lead + _flat(value[lo:hi]) + tail,
        lead + _flat(after[lo:a_hi]) + ("…" if a_hi < len(after) else ""),
    )


def _flat(s):
    return re.sub(r"\s+", " ", s)
