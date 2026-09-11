# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Build BlockSuite (Yjs) blocks from Markdown, for create and append.

Supported: headings (#..######), blockquotes (>), bullet/numbered/todo lists,
fenced code (```lang), horizontal rules (---), GitHub-flavored pipe tables (a header row,
a `---` separator row, then body rows), and plain paragraphs (one line = one paragraph).
Inline formatting is kept as plain text in v1.
"""
import re
import secrets

from pycrdt import Doc, Map, Array, Text

_ALPH = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


def nid(n=10):
    return "".join(secrets.choice(_ALPH) for _ in range(n))


def new_doc_id():
    return nid(21)


_TABLE_SEP = re.compile(r"^\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?$")


def _split_table_row(line):
    """Cells of one pipe-table row: outer pipes dropped, `\\|` unescaped, whitespace trimmed."""
    body = line.strip()
    if body.startswith("|"):
        body = body[1:]
    if body.endswith("|") and not body.endswith("\\|"):
        body = body[:-1]
    cells = re.split(r"(?<!\\)\|", body)
    return [c.replace("\\|", "|").strip() for c in cells]


def frac_index(i):
    """BlockSuite fractional-index key for position i (0-based), ascending in ASCII order:
    a0..a9, aA..aZ, aa..az (62 keys), then b00.. for larger tables."""
    if i < len(_ALPH):
        return "a" + _ALPH[i]
    i -= len(_ALPH)
    return "b" + _ALPH[i // len(_ALPH)] + _ALPH[i % len(_ALPH)]


def table_texts(spec):
    """Every non-empty cell of a table spec, in row-major order."""
    return [c for row in spec.get("rows", []) for c in row if c]


def parse_markdown(md):
    specs = []
    lines = (md or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines):
        stripped = lines[i].strip()
        if not stripped:
            i += 1
            continue
        m = re.match(r"^```(\w*)\s*$", stripped)
        if m:
            lang, body = m.group(1), []
            i += 1
            while i < len(lines) and not re.match(r"^```\s*$", lines[i].strip()):
                body.append(lines[i])
                i += 1
            i += 1
            specs.append({"flavour": "affine:code", "language": lang, "text": "\n".join(body)})
            continue
        if re.match(r"^(-{3,}|\*{3,})$", stripped):
            specs.append({"flavour": "affine:divider"})
            i += 1
            continue
        if stripped.startswith("|") and i + 1 < len(lines) and _TABLE_SEP.match(lines[i + 1].strip()):
            header = _split_table_row(stripped)
            rows = [header]
            i += 2
            while i < len(lines) and lines[i].strip().startswith("|"):
                cells = _split_table_row(lines[i])
                cells = (cells + [""] * len(header))[:len(header)]
                rows.append(cells)
                i += 1
            specs.append({"flavour": "affine:table", "rows": rows})
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            specs.append({"flavour": "affine:paragraph", "type": f"h{len(m.group(1))}", "text": m.group(2).strip()})
            i += 1
            continue
        m = re.match(r"^>\s?(.*)$", stripped)
        if m:
            specs.append({"flavour": "affine:paragraph", "type": "quote", "text": m.group(1).strip()})
            i += 1
            continue
        m = re.match(r"^[-*]\s+\[([ xX])\]\s+(.*)$", stripped)
        if m:
            specs.append({"flavour": "affine:list", "type": "todo",
                          "checked": m.group(1).lower() == "x", "text": m.group(2).strip()})
            i += 1
            continue
        m = re.match(r"^[-*+]\s+(.*)$", stripped)
        if m:
            specs.append({"flavour": "affine:list", "type": "bulleted", "text": m.group(1).strip()})
            i += 1
            continue
        m = re.match(r"^\d+\.\s+(.*)$", stripped)
        if m:
            specs.append({"flavour": "affine:list", "type": "numbered", "text": m.group(1).strip()})
            i += 1
            continue
        specs.append({"flavour": "affine:paragraph", "type": "text", "text": stripped})
        i += 1
    return specs


def _make_block(spec):
    fl = spec["flavour"]
    bid = nid(10)
    if fl == "affine:divider":
        return bid, Map({"sys:id": bid, "sys:flavour": "affine:divider", "sys:version": 1,
                         "sys:children": Array([])})
    if fl == "affine:code":
        return bid, Map({"sys:id": bid, "sys:flavour": "affine:code", "sys:version": 1,
                         "sys:children": Array([]), "prop:language": spec.get("language") or "Plain Text",
                         "prop:text": Text(spec.get("text", ""))})
    if fl == "affine:table":
        return bid, _make_table(bid, spec.get("rows") or [[""]])
    if fl == "affine:list":
        return bid, Map({"sys:id": bid, "sys:flavour": "affine:list", "sys:version": 1,
                         "sys:children": Array([]), "prop:type": spec.get("type", "bulleted"),
                         "prop:text": Text(spec.get("text", "")),
                         "prop:checked": bool(spec.get("checked", False)), "prop:collapsed": False})
    return bid, Map({"sys:id": bid, "sys:flavour": "affine:paragraph", "sys:version": 1,
                     "sys:children": Array([]), "prop:type": spec.get("type", "text"),
                     "prop:text": Text(spec.get("text", "")), "prop:collapsed": False})


def _make_table(bid, rows):
    """An affine:table block (flat props, first row is the header) from a list of cell rows."""
    props = {"sys:id": bid, "sys:flavour": "affine:table", "sys:version": 1,
             "sys:children": Array([])}
    col_ids = [nid(10) for _ in rows[0]]
    for ci, cid in enumerate(col_ids):
        props[f"prop:columns.{cid}.columnId"] = cid
        props[f"prop:columns.{cid}.order"] = frac_index(ci)
    for ri, row in enumerate(rows):
        rid = nid(10)
        props[f"prop:rows.{rid}.rowId"] = rid
        props[f"prop:rows.{rid}.order"] = frac_index(ri)
        for cid, text in zip(col_ids, row):
            props[f"prop:cells.{rid}:{cid}.text"] = Text(text)
    return Map(props)


def find_note(blocks):
    for bid in blocks.keys():
        try:
            if blocks[bid]["sys:flavour"] == "affine:note":
                return bid
        except Exception:
            continue
    return None


def append_specs_to_note(blocks, note_id, specs):
    """Create blocks from specs and append their ids to the note's children.
    Caller must already be inside doc.transaction()."""
    note_children = blocks[note_id]["sys:children"]
    for spec in specs:
        bid, blk = _make_block(spec)
        blocks[bid] = blk
        note_children.append(bid)


def children_of(blocks, bid):
    try:
        ch = blocks[bid]["sys:children"]
        return [ch[i] for i in range(len(ch))]
    except Exception:
        return []


def find_parent(blocks, target_id):
    """Id of the block whose sys:children contains target_id, or None."""
    try:
        ids = list(blocks.keys())
    except Exception:
        return None
    for bid in ids:
        if target_id in children_of(blocks, bid):
            return bid
    return None


def subtree_ids(blocks, bid):
    """bid plus every descendant id, so deletes leave no orphaned blocks behind."""
    out, stack = set(), [bid]
    while stack:
        cur = stack.pop()
        if cur in out:
            continue
        out.add(cur)
        stack.extend(children_of(blocks, cur))
    return out


def insert_specs_at(blocks, parent_id, index, specs):
    """Create blocks from specs and splice their ids into parent's children at `index`.
    Returns the new ids. Caller must already be inside doc.transaction()."""
    children = blocks[parent_id]["sys:children"]
    new_ids = []
    for offset, spec in enumerate(specs):
        bid, blk = _make_block(spec)
        blocks[bid] = blk
        children.insert(index + offset, bid)
        new_ids.append(bid)
    return new_ids


def remove_subtree(blocks, bid):
    """Detach bid from its parent and delete it and its descendants.
    Returns the set of deleted ids. Caller must already be inside doc.transaction()."""
    parent_id = find_parent(blocks, bid)
    if parent_id is not None:
        children = blocks[parent_id]["sys:children"]
        for i in range(len(children)):
            if children[i] == bid:
                del children[i]
                break
    doomed = subtree_ids(blocks, bid)
    for dead in doomed:
        try:
            del blocks[dead]
        except Exception:
            pass
    return doomed


def build_content_doc(title, markdown):
    """Build a full new page-mode doc (page -> [surface, note] -> content blocks)."""
    doc = Doc()
    blocks = doc.get("blocks", type=Map)
    page_id, surface_id, note_id = nid(10), nid(10), nid(10)
    with doc.transaction():
        blocks[page_id] = Map({"sys:id": page_id, "sys:flavour": "affine:page", "sys:version": 2,
                               "prop:title": Text(title or ""),
                               "sys:children": Array([surface_id, note_id])})
        blocks[surface_id] = Map({"sys:id": surface_id, "sys:flavour": "affine:surface", "sys:version": 5,
                                  "sys:children": Array([]),
                                  "prop:elements": Map({"type": "$blocksuite:internal:native$", "value": Map({})})})
        blocks[note_id] = Map({"sys:id": note_id, "sys:flavour": "affine:note", "sys:version": 1,
                               "sys:children": Array([]),
                               "prop:xywh": "[0,0,498,92]", "prop:index": "a0", "prop:displayMode": "both",
                               "prop:hidden": False, "prop:lockedBySelf": False,
                               "prop:background": Map({"dark": "#252525", "light": "#ffffff"}),
                               "prop:edgeless": Map({"style": Map({"shadowType": "--affine-note-shadow-box",
                                                                   "borderStyle": "none", "borderSize": 4,
                                                                   "borderRadius": 8})})})
        specs = parse_markdown(markdown) or [{"flavour": "affine:paragraph", "type": "text", "text": ""}]
        append_specs_to_note(blocks, note_id, specs)
    return doc
