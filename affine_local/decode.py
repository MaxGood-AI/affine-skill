# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Decode BlockSuite (Yjs) docs to Markdown, and read the workspace root doc."""
import re

from pycrdt import Map, Array, Text

STRUCTURAL = {"affine:page", "affine:note", "affine:surface", "affine:frame"}

# affine:table stores a flattened key-value grid on the block:
#   prop:columns.<colId>.order / .columnId
#   prop:rows.<rowId>.order / .rowId
#   prop:cells.<rowId>:<colId>.text   (a Y.Text)
# columns and rows are ordered by their fractional-index `order` strings.
_COL_ORDER = re.compile(r"^prop:columns\.(.+)\.order$")
_COL_ID = re.compile(r"^prop:columns\.(.+)\.columnId$")
_ROW_ORDER = re.compile(r"^prop:rows\.(.+)\.order$")
_ROW_ID = re.compile(r"^prop:rows\.(.+)\.rowId$")
_CELL_TEXT = re.compile(r"^prop:cells\.(.+)\.text$")


def _txt(v):
    try:
        return str(v)
    except Exception:
        return ""


def _get(block, key, default=None):
    try:
        return block[key]
    except Exception:
        return default


def title_of(doc):
    blocks = _blocks(doc)
    if blocks is None:
        return None
    for bid in blocks.keys():
        b = blocks[bid]
        if _get(b, "sys:flavour") == "affine:page":
            return _txt(_get(b, "prop:title", "")).strip()
    return None


def _blocks(doc):
    try:
        blocks = doc.get("blocks", type=Map)
        list(blocks.keys())  # probe
        return blocks
    except Exception:
        return None


def doc_to_markdown(doc):
    """Return (title, markdown). Never raises on malformed blocks."""
    blocks = _blocks(doc)
    if blocks is None:
        return None, ""
    keys = set(blocks.keys())
    page_id = None
    for bid in keys:
        if _get(blocks[bid], "sys:flavour") == "affine:page":
            page_id = bid
            break
    title = _txt(_get(blocks[page_id], "prop:title", "")).strip() if page_id else None
    lines = []
    if page_id:
        _render_children(blocks, page_id, lines, depth=0)
    md = "\n".join(lines).strip("\n")
    return title, md


def _children_ids(block):
    ch = _get(block, "sys:children")
    if ch is None:
        return []
    try:
        return [ch[i] for i in range(len(ch))]
    except Exception:
        return []


def _render_children(blocks, parent_id, lines, depth):
    parent = blocks[parent_id]
    keys = set(blocks.keys())
    for cid in _children_ids(parent):
        if cid not in keys:
            continue
        b = blocks[cid]
        fl = _get(b, "sys:flavour")
        if fl in STRUCTURAL:
            _render_children(blocks, cid, lines, depth)
            continue
        rendered = _render_block(b, fl, depth)
        if rendered is not None:
            lines.append(rendered)
            lines.append("")
        # nested content (e.g. list items with children)
        _render_children(blocks, cid, lines, depth + 1)


def _cell_md(s):
    """Collapse whitespace/newlines and escape pipes for a Markdown table cell."""
    return " ".join(str(s).split()).replace("|", "\\|")


def _render_table(b):
    """Render an affine:table block as a GitHub-flavored Markdown table.
    First row is treated as the header (AFFiNE tables have no explicit header flag)."""
    try:
        keys = list(b.keys())
    except Exception:
        return "[table]"
    cols, rows, cells = {}, {}, {}
    for k in keys:
        m = _COL_ORDER.match(k)
        if m:
            cols[m.group(1)] = _get(b, k)
            continue
        m = _COL_ID.match(k)
        if m:
            cols.setdefault(m.group(1), None)
            continue
        m = _ROW_ORDER.match(k)
        if m:
            rows[m.group(1)] = _get(b, k)
            continue
        m = _ROW_ID.match(k)
        if m:
            rows.setdefault(m.group(1), None)
            continue
        m = _CELL_TEXT.match(k)
        if m and ":" in m.group(1):
            rid, cid = m.group(1).split(":", 1)
            cells[(rid, cid)] = _txt(_get(b, k, ""))
    if not cols or not rows:
        return "[table]"
    # order by fractional-index strings (lexicographic == logical); unordered last
    col_ids = sorted(cols, key=lambda c: (cols[c] is None, cols[c] or ""))
    row_ids = sorted(rows, key=lambda r: (rows[r] is None, rows[r] or ""))

    def row_cells(r):
        return [_cell_md(cells.get((r, c), "")) for c in col_ids]

    lines = ["| " + " | ".join(row_cells(row_ids[0])) + " |",
             "| " + " | ".join("---" for _ in col_ids) + " |"]
    for r in row_ids[1:]:
        lines.append("| " + " | ".join(row_cells(r)) + " |")
    return "\n".join(lines)


def _render_block(b, fl, depth):
    indent = "  " * depth
    text = _txt(_get(b, "prop:text", "")).strip()
    if fl == "affine:paragraph":
        t = _get(b, "prop:type", "text")
        if isinstance(t, str) and t.startswith("h") and t[1:].isdigit():
            return "#" * int(t[1:]) + " " + text
        if t == "quote":
            return "> " + text
        return indent + text if depth else text
    if fl == "affine:list":
        t = _get(b, "prop:type", "bulleted")
        if t == "numbered":
            return f"{indent}1. {text}"
        if t == "todo":
            checked = _get(b, "prop:checked", False)
            return f"{indent}- [{'x' if checked else ' '}] {text}"
        return f"{indent}- {text}"
    if fl == "affine:code":
        lang = _get(b, "prop:language", "") or ""
        return f"```{lang}\n{text}\n```"
    if fl == "affine:divider":
        return "---"
    if fl == "affine:image":
        return f"![](blob:{_get(b, 'prop:sourceId', '')})"
    if fl in ("affine:attachment", "affine:bookmark") or (isinstance(fl, str) and fl.startswith("affine:embed")):
        name = _get(b, "prop:name") or _get(b, "prop:title") or _get(b, "prop:url") or ""
        return f"[{fl.split(':')[-1]}: {_txt(name)}]"
    if fl == "affine:table":
        return _render_table(b)
    if fl == "affine:database":
        return "[database]"
    return f"<!-- unsupported block: {fl} -->"


# ---- workspace root doc helpers ----

def workspace_name(root_doc):
    try:
        meta = root_doc.get("meta", type=Map)
        return _txt(meta["name"]) if "name" in list(meta.keys()) else None
    except Exception:
        return None


def list_pages(root_doc, include_trashed=False):
    """Return [{id, title, trash}] from the root doc's meta.pages, UI order."""
    out = []
    try:
        pages = root_doc.get("meta", type=Map)["pages"]
    except Exception:
        return out
    for i in range(len(pages)):
        pm = pages[i]
        try:
            trashed = bool(_get(pm, "trash"))
            if trashed and not include_trashed:
                continue
            out.append({
                "id": pm["id"],
                "title": _txt(_get(pm, "title", "")).strip(),
                "trash": trashed,
            })
        except Exception:
            continue
    return out
