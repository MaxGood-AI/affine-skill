# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Create / edit / delete docs by writing to the local store. App MUST be stopped.

Each op: compute a valid Yjs v1 delta, validate it in memory, back up the DB,
insert one (or two) `updates` rows + bump `clocks`, then integrity-check.
"""
import datetime as dt
import time

from pycrdt import Doc, Map, Array, Text

from . import blocks as B
from . import decode
from . import store


class WriteError(RuntimeError):
    pass


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


def append_doc(db_path, doc_id, markdown):
    """Append markdown blocks to the end of an existing doc's note."""
    specs = B.parse_markdown(markdown)
    if not specs:
        raise WriteError("nothing to append (empty content)")

    with store.write_conn(db_path) as con:
        store.check_schema(con)
        base_doc = _load_merged(con, doc_id)
        base_bytes = _full_state(base_doc)
        if decode.title_of(base_doc) is None and B.find_note(_reload(base_bytes)) is None:
            raise WriteError(f"doc {doc_id} not found or has no editable note")

        work = Doc()
        work.apply_update(base_bytes)
        sv = work.get_state()
        wblocks = work.get("blocks", type=Map)
        note_id = B.find_note(wblocks)
        if note_id is None:
            raise WriteError(f"doc {doc_id} has no note block to append to")
        first_text = next((s.get("text", "") for s in specs if s.get("text")), None)
        with work.transaction():
            B.append_specs_to_note(wblocks, note_id, specs)
        delta = work.get_update(sv)

        _validate_delta_contains(base_bytes, delta, first_text)

        new_ts = store.next_ts(con, doc_id)
        store.backup(db_path)
        con.execute("INSERT INTO updates (doc_id, data, created_at) VALUES (?,?,?)", (doc_id, delta, new_ts))
        con.execute("INSERT INTO clocks (doc_id, timestamp) VALUES (?,?) "
                    "ON CONFLICT(doc_id) DO UPDATE SET timestamp=excluded.timestamp", (doc_id, new_ts))
        con.commit()
        _assert_integrity(con)


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

def _reload(base_bytes):
    d = Doc()
    d.apply_update(base_bytes)
    return d.get("blocks", type=Map)


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
