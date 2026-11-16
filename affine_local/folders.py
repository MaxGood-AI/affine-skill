# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""The sidebar "Organize" folder tree, stored in the workspace's `db$folders` doc.

Every record is a root-level Y.Map of the doc, keyed by its own id:
    {"id", "type": "folder" | "doc", "parentId": <folder id> | null, "data", "index"}
`data` is the folder's name for a folder and the doc id for a doc entry; `index` is a
fractional-index key that orders siblings. A deleted record keeps only {"id", "$$DELETED": true}.

This module reads the tree and plans the writes that put a doc in an EXISTING folder.
It never creates, renames or deletes a folder.
"""
from pycrdt import Doc, Map

from . import blocks as B
from . import store

FOLDERS_DOC_ID = "db$folders"
_DIGITS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"


class FolderError(ValueError):
    pass


def load(con):
    """The workspace's folders doc (empty when the workspace has none yet)."""
    return store.load_doc(con, FOLDERS_DOC_ID)


def _get(m, key):
    try:
        return m[key]
    except Exception:
        return None


def records(doc):
    """Live records as plain dicts: [{id, type, parentId, data, index}]."""
    out = []
    for key in list(doc.keys()):
        m = doc.get(key, type=Map)
        if _get(m, "$$DELETED") or _get(m, "type") not in ("folder", "doc"):
            continue
        out.append({"id": _get(m, "id") or key, "type": m["type"], "parentId": _get(m, "parentId"),
                    "data": str(_get(m, "data") or ""), "index": _get(m, "index") or ""})
    return out


def folder_list(doc):
    """Folders with their full path and direct doc count, sorted by path.
    [{id, name, path, parent_id, docs}]"""
    recs = records(doc)
    by_id = {r["id"]: r for r in recs if r["type"] == "folder"}

    def path_of(rec):
        names, seen, cur = [], set(), rec
        while cur is not None and cur["id"] not in seen:
            seen.add(cur["id"])
            names.append(cur["data"])
            cur = by_id.get(cur["parentId"])
        return " / ".join(reversed(names))

    counts = {}
    for r in recs:
        if r["type"] == "doc":
            counts[r["parentId"]] = counts.get(r["parentId"], 0) + 1
    out = [{"id": f["id"], "name": f["data"], "path": path_of(f), "parent_id": f["parentId"],
            "docs": counts.get(f["id"], 0)} for f in by_id.values()]
    return sorted(out, key=lambda f: (f["path"].casefold(), f["id"]))


def find_folder(doc, spec):
    """The id of the one folder named `spec` (case-insensitive). `spec` may also be a full
    path such as "Parent / Child". Raises FolderError listing the folder names when the
    name matches no folder or more than one."""
    wanted = (spec or "").strip()
    if not wanted:
        raise FolderError("folder name must not be empty")
    folders = folder_list(doc)
    key = wanted.casefold()
    hits = [f for f in folders if f["name"].strip().casefold() == key]
    if not hits and "/" in wanted:
        norm = " / ".join(p.strip() for p in wanted.split("/")).casefold()
        hits = [f for f in folders if f["path"].casefold() == norm]
    if len(hits) == 1:
        return hits[0]["id"]
    listing = "; ".join(f["path"] for f in folders) or "(this workspace has no folders)"
    if not hits:
        raise FolderError(f"no folder named {wanted!r}; folders: {listing}")
    raise FolderError(f"{len(hits)} folders are named {wanted!r}; use the full path. "
                      f"Matches: {'; '.join(f['path'] for f in hits)}")


def _increment_integer(x):
    head, digs = x[0], list(x[1:])
    carry = True
    for i in range(len(digs) - 1, -1, -1):
        if not carry:
            break
        d = _DIGITS.index(digs[i]) + 1
        if d == len(_DIGITS):
            digs[i] = _DIGITS[0]
        else:
            digs[i] = _DIGITS[d]
            carry = False
    if carry:
        if head == "Z":
            return "a" + _DIGITS[0]
        if head == "z":
            return None
        h = chr(ord(head) + 1)
        if h > "a":
            digs.append(_DIGITS[0])
        else:
            digs.pop()
        return h + "".join(digs)
    return head + "".join(digs)


def _integer_part(key):
    head = key[0]
    if "a" <= head <= "z":
        n = ord(head) - ord("a") + 2
    elif "A" <= head <= "Z":
        n = ord("Z") - ord(head) + 2
    else:
        raise FolderError(f"unrecognised fractional index {key!r}")
    return key[:n]


def index_after(last):
    """A fractional-index key that sorts after `last` (None = no sibling yet)."""
    if not last:
        return "a0"
    ia = _integer_part(last)
    nxt = _increment_integer(ia)
    if nxt is None:
        raise FolderError(f"no room after fractional index {last!r}")
    return nxt


def _last_index(recs, folder_id):
    idx = [r["index"] for r in recs if r["parentId"] == folder_id and r["index"]]
    return max(idx) if idx else None


def plan_place(folders_doc, doc_id, folder_spec):
    """Place `doc_id` in the named existing folder, appended after the folder's current
    contents. Moves the doc's existing entry when it already sits in a folder.

    Returns (delta_bytes, summary_line). Planning touches nothing; the delta is verified
    against an in-memory copy before it is returned."""
    folder_id = find_folder(folders_doc, folder_spec)
    recs = records(folders_doc)
    folder_name = next(r["data"] for r in recs if r["id"] == folder_id)
    mine = [r for r in recs if r["type"] == "doc" and r["data"] == doc_id]
    if len(mine) > 1:
        raise FolderError(f"doc {doc_id} appears in {len(mine)} folders; move it in AFFiNE first")
    if mine and mine[0]["parentId"] == folder_id:
        raise FolderError(f"doc {doc_id} is already in folder {folder_name!r}")
    index = index_after(_last_index([r for r in recs if r["id"] not in {m["id"] for m in mine}], folder_id))

    base = folders_doc.get_update(b"\x00")
    work = Doc()
    work.apply_update(base)
    sv = work.get_state()
    with work.transaction():
        if mine:
            entry_id = mine[0]["id"]
            m = work.get(entry_id, type=Map)
            m["parentId"] = folder_id
            m["index"] = index
        else:
            entry_id = B.nid(21)
            m = work.get(entry_id, type=Map)
            m["id"] = entry_id
            m["type"] = "doc"
            m["parentId"] = folder_id
            m["data"] = doc_id
            m["index"] = index
    delta = work.get_update(sv)

    verify = Doc()
    verify.apply_update(base)
    verify.apply_update(delta)
    after = {r["id"]: r for r in records(verify)}
    before = {r["id"]: r for r in recs}
    placed = after.get(entry_id)
    if not placed or placed["parentId"] != folder_id or placed["data"] != doc_id or placed["type"] != "doc":
        raise FolderError("folder placement failed in-memory validation — aborting")
    for rid, was in before.items():
        if rid != entry_id and after.get(rid) != was:
            raise FolderError(f"folder placement altered record {rid} — aborting")
    if set(after) != set(before) | {entry_id}:
        raise FolderError("folder placement added or removed other records — aborting")
    verb = "move" if mine else "place"
    return delta, f"{verb} {doc_id} in folder {folder_name!r}"
