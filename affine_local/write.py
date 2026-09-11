# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Create / edit / delete docs by writing to the local store. App MUST be stopped.

Each op: compute a valid Yjs v1 delta, validate it in memory, back up the DB,
insert one (or two) `updates` rows + bump `clocks`, then integrity-check.

In-place edits (replace / insert / delete-block / set-title) are split into a
`plan_*` half that builds and fully validates the delta against an in-memory copy,
and `commit()`, which writes it. Planning touches nothing, so `--dry-run` can run
against a read-only copy of the store while AFFiNE is still open.
"""
import time

from pycrdt import Doc, Map, Array, Text

from . import blocks as B
from . import decode
from . import store
from . import textops as T

STRUCTURAL = {"affine:page", "affine:note", "affine:surface", "affine:frame"}


class WriteError(RuntimeError):
    pass


class Change:
    """One or more validated deltas, ready to commit, plus a human-readable summary."""

    __slots__ = ("deltas", "summary")

    def __init__(self, deltas, summary):
        self.deltas = deltas  # [(doc_id, delta_bytes)]
        self.summary = summary  # [str]


def _load_merged(con, doc_id):
    return store.load_doc(con, doc_id)


def _full_state(doc):
    return doc.get_update(b"\x00")


def _strip_leading_h1(markdown):
    """Drop a leading '# ...' line — the doc title is stored separately (AFFiNE convention)."""
    lines = (markdown or "").replace("\r\n", "\n").split("\n")
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and lines[i].strip().startswith("# "):
        return "\n".join(lines[i + 1:])
    return markdown


def new_doc(db_path, root_id, server_id, workspace_id, title, markdown):
    """Create a new doc + register it in the workspace root doc's meta.pages.
    Returns the new doc id. The app self-heals the `spaces` subdoc entry on next launch."""
    content = B.build_content_doc(title, _strip_leading_h1(markdown))
    content_bytes = _full_state(content)
    doc_id = B.new_doc_id()
    now_ms = int(time.time() * 1000)

    with store.write_conn(db_path) as con:
        store.check_schema(con)
        new_ts = store.next_ts(con, root_id)  # derive from root's recent clock

        root = _load_merged(con, root_id)
        sv = root.get_state()
        pages = root.get("meta", type=Map)["pages"]
        n_before = len(pages)
        with root.transaction():
            pages.append(Map({"id": doc_id, "title": Text(title or ""),
                              "createDate": float(now_ms), "updatedDate": float(now_ms),
                              "tags": Array([])}))
        root_delta = root.get_update(sv)

        # validate: page registered, content decodes with our title
        _validate_new(root, doc_id, n_before, content, title)

        store.backup(db_path)
        con.execute("INSERT INTO updates (doc_id, data, created_at) VALUES (?,?,?)",
                    (doc_id, content_bytes, new_ts))
        con.execute("INSERT INTO clocks (doc_id, timestamp) VALUES (?,?)", (doc_id, new_ts))
        con.execute("INSERT INTO updates (doc_id, data, created_at) VALUES (?,?,?)",
                    (root_id, root_delta, new_ts))
        con.execute("INSERT INTO clocks (doc_id, timestamp) VALUES (?,?) "
                    "ON CONFLICT(doc_id) DO UPDATE SET timestamp=excluded.timestamp", (root_id, new_ts))
        con.commit()
        _assert_integrity(con)
    return doc_id


def commit(db_path, change):
    """Back up, write every delta in `change` as an updates row, bump clocks, verify."""
    if not change.deltas:
        raise WriteError("nothing to commit")
    with store.write_conn(db_path) as con:
        store.check_schema(con)
        store.backup(db_path)
        for doc_id, delta in change.deltas:
            ts = store.next_ts(con, doc_id)
            con.execute("INSERT INTO updates (doc_id, data, created_at) VALUES (?,?,?)",
                        (doc_id, delta, ts))
            con.execute("INSERT INTO clocks (doc_id, timestamp) VALUES (?,?) "
                        "ON CONFLICT(doc_id) DO UPDATE SET timestamp=excluded.timestamp",
                        (doc_id, ts))
        con.commit()
        _assert_integrity(con)


# ---- in-place edit planners ----

def plan_replace(base_doc, doc_id, old, new, replace_all=False, in_block=None):
    """Replace literal text `old` with `new` inside the Y.Text that holds it.

    Splicing the existing Y.Text (rather than rebuilding the block from Markdown)
    keeps block identity, inline formatting and table structure intact, and is the
    only way to edit table cells — Markdown round-tripping cannot rebuild those.

    `in_block` scopes the replace to the single block containing that anchor text,
    which is how you target one occurrence of a phrase that legitimately recurs
    elsewhere in the doc (a term and the rule that quotes it, say).
    """
    if not old:
        raise WriteError("--replace requires non-empty text")
    if old == new:
        raise WriteError("--replace and --with are identical; nothing to do")

    base_bytes = _full_state(base_doc)
    base_blocks = base_doc.get("blocks", type=Map)
    before_values = T.current_values(base_blocks)
    before_ids = _block_ids(base_blocks)

    work = Doc()
    work.apply_update(base_bytes)
    sv = work.get_state()
    wblocks = work.get("blocks", type=Map)
    fields = T.text_fields(wblocks)
    if in_block:
        scope_id = _unique_anchor(wblocks, in_block)
        fields = [f for f in fields if f.block_id == scope_id]
    matches = T.find_matches(fields, old)

    if not matches:
        if in_block:
            raise WriteError(f"{old!r} was not found in the block matching {in_block!r}")
        raise WriteError(_no_match_message(old, before_values))
    if len(matches) > 1 and not replace_all:
        where = "; ".join(sorted({f"{m.field.flavour} {m.field.block_id}" for m in matches})[:6])
        raise WriteError(
            f"{len(matches)} occurrences of {old!r} — pass --all to replace them all, "
            f"extend the search text, or scope with --in-block ANCHOR "
            f"(in: {where})"
        )

    expected = T.expected_values(matches, new)
    summary = [
        f"{m.field.flavour} {m.field.ref}\n    - {b}\n    + {a}"
        for m in matches
        for b, a in [T.context(m.field.value, m.index, m.length, new)]
    ]

    # descending index order: each splice is computed against the load-time value,
    # so later matches must be applied before earlier ones shift their offsets.
    with work.transaction():
        for m in sorted(matches, key=lambda x: (x.field.ref, x.index), reverse=True):
            T.apply_match(m, new)
    delta = work.get_update(sv)

    verify = _verify(base_bytes, delta)
    vblocks = verify.get("blocks", type=Map)
    after_values = T.current_values(vblocks)
    if _block_ids(vblocks) != before_ids:
        raise WriteError("replace altered the block structure — aborting")
    if set(after_values) != set(before_values):
        raise WriteError("replace added or removed text fields — aborting")
    for ref, want in expected.items():
        if after_values.get(ref) != want:
            raise WriteError(f"replace produced unexpected text in {ref} — aborting")
    for ref, was in before_values.items():
        if ref not in expected and after_values.get(ref) != was:
            raise WriteError(f"replace altered untargeted text in {ref} — aborting")

    return Change([(doc_id, delta)], summary)


def plan_insert(base_doc, doc_id, anchor, markdown, before=False):
    """Insert Markdown blocks immediately after (or before) the block holding `anchor`."""
    specs = B.parse_markdown(markdown)
    if not specs:
        raise WriteError("nothing to insert (empty content)")

    base_bytes = _full_state(base_doc)
    base_blocks = base_doc.get("blocks", type=Map)
    before_ids = _block_ids(base_blocks)

    work = Doc()
    work.apply_update(base_bytes)
    sv = work.get_state()
    wblocks = work.get("blocks", type=Map)

    anchor_id = _unique_anchor(wblocks, anchor)
    parent_id = B.find_parent(wblocks, anchor_id)
    if parent_id is None:
        raise WriteError(f"anchor block {anchor_id} has no parent — cannot position an insert")
    siblings = B.children_of(wblocks, parent_id)
    at = siblings.index(anchor_id) + (0 if before else 1)

    with work.transaction():
        new_ids = B.insert_specs_at(wblocks, parent_id, at, specs)
    delta = work.get_update(sv)

    verify = _verify(base_bytes, delta)
    vblocks = verify.get("blocks", type=Map)
    if _block_ids(vblocks) != before_ids | set(new_ids):
        raise WriteError("insert changed blocks other than the ones added — aborting")
    want_children = siblings[:at] + new_ids + siblings[at:]
    if B.children_of(vblocks, parent_id) != want_children:
        raise WriteError("inserted blocks did not land in the expected position — aborting")
    _t, md = decode.doc_to_markdown(verify)
    for text in _spec_texts(specs):
        if text not in md:
            raise WriteError("inserted content failed in-memory validation — aborting")

    where = "before" if before else "after"
    return Change([(doc_id, delta)],
                  [f"insert {len(specs)} block(s) {where} {_flavour(wblocks, anchor_id)} {anchor_id}"])


def plan_delete_block(base_doc, doc_id, anchor):
    """Delete the single block holding `anchor`, plus any nested children it owns."""
    base_bytes = _full_state(base_doc)
    base_blocks = base_doc.get("blocks", type=Map)
    before_ids = _block_ids(base_blocks)
    before_values = T.current_values(base_blocks)

    work = Doc()
    work.apply_update(base_bytes)
    sv = work.get_state()
    wblocks = work.get("blocks", type=Map)

    anchor_id = _unique_anchor(wblocks, anchor)
    flavour = _flavour(wblocks, anchor_id)
    if flavour in STRUCTURAL:
        raise WriteError(f"refusing to delete structural block {anchor_id} ({flavour})")
    parent_id = B.find_parent(wblocks, anchor_id)
    if parent_id is None:
        raise WriteError(f"block {anchor_id} has no parent — refusing to delete")
    siblings = B.children_of(wblocks, parent_id)
    doomed = B.subtree_ids(wblocks, anchor_id)
    preview = _text_preview(wblocks, anchor_id)

    with work.transaction():
        B.remove_subtree(wblocks, anchor_id)
    delta = work.get_update(sv)

    verify = _verify(base_bytes, delta)
    vblocks = verify.get("blocks", type=Map)
    if _block_ids(vblocks) != before_ids - doomed:
        raise WriteError("delete removed blocks other than the target subtree — aborting")
    if B.children_of(vblocks, parent_id) != [c for c in siblings if c != anchor_id]:
        raise WriteError("delete altered sibling order — aborting")
    after_values = T.current_values(vblocks)
    for ref, was in before_values.items():
        if ref.split("/", 1)[0] in doomed:
            continue
        if after_values.get(ref) != was:
            raise WriteError(f"delete altered surviving text in {ref} — aborting")

    n = len(doomed)
    extra = f" (+{n - 1} nested)" if n > 1 else ""
    return Change([(doc_id, delta)], [f"delete {flavour} {anchor_id}{extra}: {preview}"])


def plan_set_title(base_doc, doc_id, root_doc, root_id, new_title):
    """Retitle a doc: the page block's prop:title AND the workspace root's page meta.

    Both must move together or the sidebar and the open document disagree.
    """
    new_title = (new_title or "").strip()
    if not new_title:
        raise WriteError("--set-title requires a non-empty title")
    old_title = decode.title_of(base_doc)
    if old_title == new_title:
        raise WriteError("title is already set to that value")

    base_bytes = _full_state(base_doc)
    work = Doc()
    work.apply_update(base_bytes)
    sv = work.get_state()
    wblocks = work.get("blocks", type=Map)
    page_id = next((b for b in wblocks.keys()
                    if _flavour(wblocks, b) == "affine:page"), None)
    if page_id is None:
        raise WriteError(f"doc {doc_id} has no page block to retitle")
    with work.transaction():
        wblocks[page_id]["prop:title"] = Text(new_title)
    doc_delta = work.get_update(sv)

    verify = _verify(base_bytes, doc_delta)
    if decode.title_of(verify) != new_title:
        raise WriteError("title change failed in-memory validation — aborting")
    if _block_ids(verify.get("blocks", type=Map)) != _block_ids(base_doc.get("blocks", type=Map)):
        raise WriteError("title change altered the block structure — aborting")

    root_bytes = _full_state(root_doc)
    before_pages = _pages_state(root_doc)
    if doc_id not in before_pages:
        raise WriteError(f"doc {doc_id} is not registered in this workspace")
    rwork = Doc()
    rwork.apply_update(root_bytes)
    rsv = rwork.get_state()
    pages = rwork.get("meta", type=Map)["pages"]
    with rwork.transaction():
        for i in range(len(pages)):
            if pages[i]["id"] == doc_id:
                pages[i]["title"] = Text(new_title)
                pages[i]["updatedDate"] = float(int(time.time() * 1000))
                break
    root_delta = rwork.get_update(rsv)

    rverify = _verify(root_bytes, root_delta)
    after_pages = _pages_state(rverify)
    if after_pages.get(doc_id, {}).get("title") != new_title:
        raise WriteError("root meta title change failed in-memory validation — aborting")
    if set(after_pages) != set(before_pages):
        raise WriteError("title change added or removed pages — aborting")
    for pid, was in before_pages.items():
        if pid != doc_id and after_pages[pid] != was:
            raise WriteError(f"title change altered page meta for {pid} — aborting")

    return Change([(doc_id, doc_delta), (root_id, root_delta)],
                  [f"retitle {doc_id}: {old_title!r} -> {new_title!r}"])


def plan_append(base_doc, doc_id, markdown):
    """Append Markdown blocks to the end of the doc's note (planned, not written)."""
    specs = B.parse_markdown(markdown)
    if not specs:
        raise WriteError("nothing to append (empty content)")

    base_bytes = _full_state(base_doc)
    work = Doc()
    work.apply_update(base_bytes)
    sv = work.get_state()
    wblocks = work.get("blocks", type=Map)
    note_id = B.find_note(wblocks)
    if note_id is None:
        raise WriteError(f"doc {doc_id} not found or has no editable note")
    first_text = next(iter(_spec_texts(specs)), None)
    with work.transaction():
        B.append_specs_to_note(wblocks, note_id, specs)
    delta = work.get_update(sv)

    _validate_delta_contains(base_bytes, delta, first_text)
    return Change([(doc_id, delta)], [f"append {len(specs)} block(s) to {doc_id}"])


def trash_doc(db_path, root_id, doc_id):
    """Move a doc to Trash by setting trash=true on its page-meta in the root doc."""
    trash_ms = int(time.time() * 1000)
    with store.write_conn(db_path) as con:
        store.check_schema(con)
        root = _load_merged(con, root_id)
        base_bytes = _full_state(root)
        before = _pages_state(root)
        if doc_id not in before:
            raise WriteError(f"doc {doc_id} is not registered in this workspace")
        if before[doc_id]["trash"]:
            raise WriteError(f"doc {doc_id} is already in Trash")

        work = Doc()
        work.apply_update(base_bytes)
        sv = work.get_state()
        pages = work.get("meta", type=Map)["pages"]
        with work.transaction():
            for i in range(len(pages)):
                if pages[i]["id"] == doc_id:
                    pages[i]["trash"] = True
                    pages[i]["trashDate"] = float(trash_ms)
                    break
        delta = work.get_update(sv)

        verify = Doc()
        verify.apply_update(base_bytes)
        verify.apply_update(delta)
        after = _pages_state(verify)
        if not (after.get(doc_id, {}).get("trash") is True):
            raise WriteError("trash delta did not take effect")
        if set(before) != set(after) or any(
            k != doc_id and before[k]["trash"] != after[k]["trash"] for k in before
        ):
            raise WriteError("trash delta would alter other docs — aborting")

        new_ts = store.next_ts(con, root_id)
        store.backup(db_path)
        con.execute("INSERT INTO updates (doc_id, data, created_at) VALUES (?,?,?)", (root_id, delta, new_ts))
        con.execute("INSERT INTO clocks (doc_id, timestamp) VALUES (?,?) "
                    "ON CONFLICT(doc_id) DO UPDATE SET timestamp=excluded.timestamp", (root_id, new_ts))
        con.commit()
        _assert_integrity(con)


# ---- helpers ----

def _verify(base_bytes, delta):
    """The document as it will exist once `delta` is applied."""
    v = Doc()
    v.apply_update(base_bytes)
    v.apply_update(delta)
    return v


def _spec_texts(specs):
    """Texts a rendered doc must contain for these specs: block text, or every table cell
    as the Markdown renderer escapes it."""
    out = []
    for spec in specs:
        if spec["flavour"] == "affine:table":
            out.extend(decode._cell_md(c) for c in B.table_texts(spec))
        elif spec.get("text"):
            out.append(spec["text"])
    return out


def _block_ids(blocks):
    try:
        return set(blocks.keys())
    except Exception:
        return set()


def _flavour(blocks, bid):
    try:
        return blocks[bid]["sys:flavour"]
    except Exception:
        return "?"


def _text_preview(blocks, bid, width=80):
    for f in T.text_fields(blocks):
        if f.block_id == bid:
            return T._flat(f.value)[:width]
    return ""


def _unique_anchor(blocks, anchor):
    """The id of the one block whose text contains `anchor`; error if 0 or >1.

    A block whose whole text IS the anchor wins over blocks that merely contain it,
    so a short anchor can still name a table-of-contents entry or a heading without
    colliding with the longer prose that quotes it.
    """
    if not (anchor or "").strip():
        raise WriteError("anchor text must not be empty")
    fields = T.text_fields(blocks)
    exact = []
    for f in fields:
        if f.value.strip() == anchor.strip() and f.block_id not in exact:
            exact.append(f.block_id)
    if len(exact) == 1:
        return exact[0]
    hits = []
    for f in fields:
        if anchor in f.value and f.block_id not in hits:
            hits.append(f.block_id)
    if not hits:
        raise WriteError(f"no block contains {anchor!r}")
    if len(hits) > 1:
        preview = "; ".join(f"{b} ({_text_preview(blocks, b, 40)})" for b in hits[:5])
        raise WriteError(
            f"{len(hits)} blocks contain {anchor!r} — extend the anchor to match "
            f"exactly one (matched: {preview})"
        )
    return hits[0]


def _no_match_message(old, before_values):
    """Explain a failed match, including the block-boundary case that trips people up."""
    if "\n" in old:
        first = old.split("\n", 1)[0].strip()
        if first and any(first in v for v in before_values.values()):
            return (
                f"{old!r} was not found in any single block. A match cannot span block "
                "boundaries — each paragraph, list item and heading is its own block. "
                "Replace one block's text at a time, or use --insert-after/--delete-block."
            )
    return f"{old!r} was not found in this doc"


def _pages_state(root):
    pages = root.get("meta", type=Map)["pages"]
    out = {}
    for i in range(len(pages)):
        pm = pages[i]
        try:
            out[pm["id"]] = {"trash": bool(pm.get("trash")), "title": str(pm.get("title", ""))}
        except Exception:
            pass
    return out


def _validate_new(root, doc_id, n_before, content, title):
    pages = root.get("meta", type=Map)["pages"]
    if len(pages) != n_before + 1:
        raise WriteError("root registration produced wrong page count")
    if not any(pages[i]["id"] == doc_id for i in range(len(pages))):
        raise WriteError("new doc id not found in meta.pages after registration")
    t, _md = decode.doc_to_markdown(content)
    if (title or "") and t != (title or "").strip():
        raise WriteError("content doc title mismatch")


def _validate_delta_contains(base_bytes, delta, needle):
    if not needle:
        return
    v = Doc()
    v.apply_update(base_bytes)
    v.apply_update(delta)
    _t, md = decode.doc_to_markdown(v)
    if needle not in md:
        raise WriteError("append delta failed in-memory validation")


def _assert_integrity(con):
    res = con.execute("PRAGMA integrity_check;").fetchone()[0]
    if res != "ok":
        raise WriteError(f"integrity_check failed after write: {res}")
