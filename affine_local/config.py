# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Discover and resolve AFFiNE workspaces from the desktop app's local data.

The skill is workspace-aware: reads (list/search/read) and doc-id operations
(read/edit/delete) span ALL workspaces by default so a doc is found wherever it
lives. Only `new` needs a single target workspace (default or --workspace)."""
import json

from . import decode
from . import store


class Workspace:
    def __init__(self, server_id, workspace_id, db_path, name, kind):
        self.server_id = server_id
        self.workspace_id = workspace_id
        self.db_path = db_path
        self.name = name
        self.kind = kind  # "cloud" | "local"

    @property
    def root_id(self):
        return self.workspace_id

    def as_dict(self):
        return {"name": self.name, "workspace_id": self.workspace_id,
                "server_id": self.server_id, "kind": self.kind, "db_path": self.db_path}


def _load_config():
    try:
        return json.loads(store.SKILL_DIR.joinpath("config.json").read_text())
    except Exception:
        return {}


def list_workspaces():
    """All workspaces the desktop app knows about, in a stable (sorted) order."""
    out = []
    base = store.WORKSPACES_DIR
    if not base.exists():
        return out
    for server_dir in sorted(base.iterdir()):
        if not server_dir.is_dir():
            continue
        server_id = server_dir.name
        kind = "local" if server_id == "local" else "cloud"
        for ws_dir in sorted(server_dir.iterdir()):
            db = ws_dir / "storage.db"
            if not db.exists():
                continue
            workspace_id = ws_dir.name
            name, has = None, 0
            try:
                with store.read_conn(db) as con:
                    has = con.execute("SELECT count(*) FROM snapshots").fetchone()[0]
                    if has:
                        name = decode.workspace_name(store.load_doc(con, workspace_id))
            except Exception:
                has = 0
            if not has:
                continue  # vestigial/empty store
            out.append(Workspace(server_id, workspace_id, str(db), name or "(unnamed)", kind))
    return out


def cloud_workspaces():
    return [w for w in list_workspaces() if w.kind == "cloud"]


def _match_one(wss, spec):
    for w in wss:
        if w.workspace_id == spec:
            return w
    matches = [w for w in wss if spec.lower() in (w.name or "").lower()]
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise ValueError(f"ambiguous workspace {spec!r}: {[w.name for w in matches]}")
    raise ValueError(f"no workspace matching {spec!r}; available: {[w.name for w in wss]}")


def resolve_one(spec=None):
    """Resolve exactly one target workspace — for `new`, which must pick a home."""
    wss = list_workspaces()
    if not wss:
        raise ValueError("no AFFiNE workspaces found (is the desktop app installed and signed in?)")
    if spec:
        return _match_one(wss, spec)
    default = _load_config().get("default_workspace")
    if default:
        return _match_one(wss, default)
    cloud = [w for w in wss if w.kind == "cloud"]
    if len(cloud) == 1:
        return cloud[0]
    raise ValueError(
        "multiple workspaces — pass --workspace or set default_workspace in config.json; "
        f"available: {[w.name for w in cloud]}"
    )


def resolve_scope(spec=None):
    """Workspaces to sweep for reads — one if --workspace given, else ALL."""
    if spec:
        return [_match_one(list_workspaces(), spec)]
    wss = list_workspaces()
    if not wss:
        raise ValueError("no AFFiNE workspaces found (is the desktop app installed and signed in?)")
    return wss


def locate(doc_id, spec=None):
    """Find the workspace that contains doc_id. Searches ALL workspaces unless --workspace given."""
    scope = resolve_scope(spec)
    found = []
    for w in scope:
        try:
            with store.read_conn(w.db_path) as con:
                store.check_schema(con)
                if store.has_doc(con, doc_id):
                    found.append(w)
        except Exception:
            continue
    if not found:
        where = f"workspace {spec!r}" if spec else "any workspace"
        raise ValueError(f"doc {doc_id} not found in {where}")
    return found[0]  # doc ids are unique; first match wins
