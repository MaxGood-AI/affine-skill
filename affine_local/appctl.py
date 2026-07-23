# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""Control the AFFiNE desktop app lifecycle and drive the sync recipe.

Sync recipe (validated on v0.27.3): quit -> write -> relaunch -> activate ->
the app pushes local changes to the server within a few seconds.
"""
import subprocess
import time

from . import store

APP = "AFFiNE"


def _sh(args, timeout=15):
    return subprocess.run(args, capture_output=True, text=True, timeout=timeout)


def is_running():
    out = _sh(["ps", "-Ao", "comm"]).stdout
    return any("AFFiNE.app" in line for line in out.splitlines())


def quit_app(timeout=25):
    """Gracefully quit AFFiNE (full quit, closes the DB). Returns True if stopped."""
    _sh(["osascript", "-e", f'tell application "{APP}" to quit'])
    for _ in range(timeout):
        if not is_running():
            return True
        time.sleep(1)
    return not is_running()


def ensure_stopped():
    if is_running() and not quit_app():
        raise RuntimeError("AFFiNE is still running and would not quit; writes need it stopped")


def relaunch():
    _sh(["open", "-a", APP])


def activate():
    _sh(["osascript", "-e", f'tell application "{APP}" to activate'])


def open_workspace(workspace):
    """Open a workspace in a new tab via AFFiNE's deep link, engaging its sync engine.
    Only the foreground/tabbed workspaces sync, so non-active ones need this to push.
    (Scheme is `affine://` for stable builds.)"""
    _sh(["open", f"affine://app/workspace/{workspace.workspace_id}?new-tab=true"])


def sync(workspaces, timeout=120):
    """Relaunch + foreground AFFiNE and wait until EVERY given workspace has no unpushed
    docs. Workspaces that don't sync on their own (not the foreground tab) are engaged by
    opening them via deep link. Returns (synced: bool, seconds: int, remaining: [Workspace])."""
    relaunch()
    time.sleep(3)
    activate()
    opened = set()
    waited = 3
    while waited < timeout:
        time.sleep(3)
        waited += 3
        remaining = [w for w in workspaces if unpushed(w) > 0]
        if not remaining:
            return True, waited, []
        # after a short grace for the foreground workspace, engage any stragglers by tab
        if waited >= 6:
            for w in remaining:
                if w.workspace_id not in opened:
                    open_workspace(w)
                    opened.add(w.workspace_id)
        activate()
    return False, waited, [w for w in workspaces if unpushed(w) > 0]


def unpushed(workspace):
    try:
        with store.read_conn(workspace.db_path) as con:
            return store.unpushed_count(con, workspace.server_id)
    except Exception:
        return 0
