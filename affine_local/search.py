# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Keyword search across one or more workspaces' docs (decode + match, no server index)."""
from . import decode
from . import store


def search(workspaces, query, limit=10):
    """Search every workspace in `workspaces`; results carry their workspace name."""
    q = (query or "").lower().strip()
    if not q:
        return []
    terms = q.split()
    results = []
    for ws in workspaces:
        try:
            with store.read_conn(ws.db_path) as con:
                store.check_schema(con)
                for doc_id in store.content_doc_ids(con):
                    doc = store.load_doc(con, doc_id)
                    title, md = decode.doc_to_markdown(doc)
                    if title is None and not md:
                        continue
                    hay = f"{title or ''}\n{md}".lower()
                    score = sum(hay.count(t) for t in terms)
                    if score == 0:
                        continue
                    results.append({
                        "workspace": ws.name,
                        "doc_id": doc_id,
                        "title": title or "(untitled)",
                        "score": score,
                        "snippet": _snippet(md, terms),
                    })
        except Exception:
            continue
    results.sort(key=lambda r: (-r["score"], r["workspace"], r["title"]))
    return results[:limit]


def _snippet(md, terms, width=140):
    low = md.lower()
    pos = -1
    for t in terms:
        p = low.find(t)
        if p != -1 and (pos == -1 or p < pos):
            pos = p
    if pos == -1:
        return md[:width].replace("\n", " ").strip()
    start = max(0, pos - width // 3)
    seg = md[start:start + width].replace("\n", " ").strip()
    return ("…" if start else "") + seg + ("…" if start + width < len(md) else "")
