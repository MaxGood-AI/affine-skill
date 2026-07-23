# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Low-level access to AFFiNE desktop's local SQLite stores.

Reads go through a WAL-safe copy so they are consistent even while the app runs.
Writes are serialized by a lock and always preceded by a pruned backup.
"""
import contextlib
import datetime as dt
import fcntl
import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

from pycrdt import Doc

APP_DATA = Path.home() / "Library" / "Application Support" / "AFFiNE"
WORKSPACES_DIR = APP_DATA / "workspaces"
GLOBAL_STATE = APP_DATA / "global-state.json"
SKILL_DIR = Path(__file__).resolve().parent.parent
LOCK_PATH = SKILL_DIR / ".affine.lock"
MAX_BACKUPS = 5
EXPECTED_TABLES = {"snapshots", "updates", "clocks", "blobs", "peer_clocks"}
TS_FMT_MS = "%Y-%m-%d %H:%M:%S.%f"
TS_FMT_S = "%Y-%m-%d %H:%M:%S"


def fmt_ts(d):
    return f"{d:%Y-%m-%d %H:%M:%S}.{d.microsecond // 1000:03d}"


def parse_ts(s):
    for f in (TS_FMT_MS, TS_FMT_S):
        try:
            return dt.datetime.strptime(s, f)
        except (ValueError, TypeError):
            continue
    raise ValueError(f"unparseable timestamp {s!r}")


@contextlib.contextmanager
def read_conn(db_path):
    """Open a WAL-consistent read-only copy of the store; cleans up on exit."""
    tmp = tempfile.mkdtemp(prefix="affine-read-")
    try:
        base = os.path.join(tmp, "s.db")
        shutil.copy2(db_path, base)
        for ext in ("-wal", "-shm"):
            try:
                shutil.copy2(str(db_path) + ext, base + ext)
            except FileNotFoundError:
                pass
        con = sqlite3.connect(base)
        try:
            yield con
        finally:
            con.close()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


@contextlib.contextmanager
def write_conn(db_path):
    con = sqlite3.connect(str(db_path))
    try:
        yield con
    finally:
        con.close()


@contextlib.contextmanager
def lock():
    """Serialize skill operations across concurrent invocations/agents."""
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh, fcntl.LOCK_EX)
        yield
    finally:
        fcntl.flock(fh, fcntl.LOCK_UN)
        fh.close()


def check_schema(con):
    tbls = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    missing = EXPECTED_TABLES - tbls
    if missing:
        raise RuntimeError(
            f"storage.db is missing expected tables {sorted(missing)}; "
            "the AFFiNE version/schema may have changed — aborting for safety"
        )


def load_doc(con, doc_id):
    """Merge a doc's snapshot + newer update rows into one pycrdt Doc (the app's read model)."""
    snap = con.execute("SELECT data FROM snapshots WHERE doc_id=?", (doc_id,)).fetchone()
    ups = con.execute(
        "SELECT data FROM updates WHERE doc_id=? ORDER BY created_at ASC", (doc_id,)
    ).fetchall()
    doc = Doc()
    if snap:
        doc.apply_update(snap[0])
    for (u,) in ups:
        doc.apply_update(u)
    return doc


def has_doc(con, doc_id):
    """True if this store contains any data for doc_id (snapshot/update/clock row)."""
    for tbl in ("snapshots", "updates", "clocks"):
        if con.execute(f"SELECT 1 FROM {tbl} WHERE doc_id=? LIMIT 1", (doc_id,)).fetchone():
            return True
    return False


def content_doc_ids(con):
    ids = set()
    for tbl in ("snapshots", "updates", "clocks"):
        for (d,) in con.execute(f"SELECT DISTINCT doc_id FROM {tbl}"):
            ids.add(d)
    return {d for d in ids if not d.startswith("db$") and not d.startswith("userdata$")}


def doc_max_ts(con, doc_id):
    """Greatest known timestamp for a doc across clocks/updates/snapshot (for monotonic writes)."""
    cands = []
    for q in (
        "SELECT timestamp FROM clocks WHERE doc_id=?",
        "SELECT max(created_at) FROM updates WHERE doc_id=?",
        "SELECT updated_at FROM snapshots WHERE doc_id=?",
    ):
        r = con.execute(q, (doc_id,)).fetchone()
        if r and r[0]:
            cands.append(parse_ts(r[0]))
    return max(cands) if cands else None


def next_ts(con, doc_id):
    """A unique, monotonic timestamp string strictly greater than the doc's current max."""
    cur = doc_max_ts(con, doc_id)
    base = cur if cur else parse_ts("2026-01-01 00:00:00.000")
    return fmt_ts(base + dt.timedelta(seconds=5))


def backup(db_path):
    """Copy db(+wal+shm) to a pruned set of .skillbak files. Returns the backup path."""
    db_path = str(db_path)
    stamp = fmt_ts(dt.datetime.fromtimestamp(os.path.getmtime(db_path))).replace(":", "").replace(" ", "-").replace(".", "")
    dst = f"{db_path}.skillbak-{stamp}"
    shutil.copy2(db_path, dst)
    for ext in ("-wal", "-shm"):
        if os.path.exists(db_path + ext):
            shutil.copy2(db_path + ext, dst + ext)
    _prune_backups(db_path)
    return dst


def _prune_backups(db_path):
    baks = sorted(Path(db_path).parent.glob(Path(db_path).name + ".skillbak-*"))
    mains = [b for b in baks if not (b.name.endswith("-wal") or b.name.endswith("-shm"))]
    for old in mains[:-MAX_BACKUPS]:
        old.unlink(missing_ok=True)
        Path(str(old) + "-wal").unlink(missing_ok=True)
        Path(str(old) + "-shm").unlink(missing_ok=True)


def peer_id_for(server_id):
    return f"cloud:{server_id}"


def unpushed_count(con, server_id):
    """Number of docs whose local clock is ahead of the pushed clock (dirty/unsynced)."""
    rows = con.execute(
        "SELECT c.doc_id, c.timestamp, p.pushed_clock FROM clocks c "
        "LEFT JOIN peer_clocks p ON c.doc_id=p.doc_id AND p.peer=?",
        (peer_id_for(server_id),),
    ).fetchall()
    n = 0
    for _doc, clk, pushed in rows:
        cd = parse_ts(clk) if clk else None
        pd = parse_ts(pushed) if pushed else None
        if cd and (pd is None or cd > pd):
            n += 1
    return n
