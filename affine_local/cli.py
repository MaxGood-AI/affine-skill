# Copyright (c) 2026 14334876 Canada Inc. (dba. MaxGood.work)
# SPDX-License-Identifier: BSD-3-Clause
"""affine — deterministic local AFFiNE doc management (read/search/create/edit/delete/sync)."""
import argparse
import json
import sys

from . import appctl
from . import config
from . import decode
from . import search as search_mod
from . import store
from . import write as write_mod


def _err(msg, code=1):
    print(f"error: {msg}", file=sys.stderr)
    sys.exit(code)


def _note(msg):
    print(msg, file=sys.stderr)


def cmd_workspaces(args):
    wss = config.list_workspaces()
    if args.json:
        print(json.dumps([w.as_dict() for w in wss], indent=2))
        return
    for w in wss:
        print(f"{w.kind:5}  {w.workspace_id}  {w.name}")


def cmd_list(args):
    wss = config.resolve_scope(args.workspace)
    blocks, rows = [], []
    for ws in wss:
        with store.read_conn(ws.db_path) as con:
            store.check_schema(con)
            root = store.load_doc(con, ws.root_id)
        pages = decode.list_pages(root, include_trashed=args.all)
        shown = pages[: args.limit] if args.limit else pages
        blocks.append((ws, len(pages), shown))
        rows.extend({"workspace": ws.name, **p} for p in shown)
    if args.json:
        print(json.dumps(rows, indent=2))
        return
    for ws, total, shown in blocks:
        extra = f" (showing {len(shown)})" if len(shown) != total else ""
        print(f"# {ws.name} — {total} docs{extra}")
        for p in shown:
            flag = " [trash]" if p["trash"] else ""
            print(f"{p['id']}\t{p['title'] or '(untitled)'}{flag}")
        print()


def cmd_read(args):
    ws = config.locate(args.doc_id, args.workspace)
    with store.read_conn(ws.db_path) as con:
        store.check_schema(con)
        doc = store.load_doc(con, args.doc_id)
    title, md = decode.doc_to_markdown(doc)
    if title is None and not md:
        _err(f"doc {args.doc_id} is empty")
    if args.json:
        print(json.dumps({"workspace": ws.name, "doc_id": args.doc_id, "title": title, "markdown": md}))
        return
    _note(f"(from workspace: {ws.name})")
    if title:
        print(f"# {title}\n")
    print(md)


def cmd_search(args):
    wss = config.resolve_scope(args.workspace)
    res = search_mod.search(wss, args.query, args.limit)
    if args.json:
        print(json.dumps(res, indent=2))
        return
    if not res:
        print("(no matches)")
        return
    for r in res:
        print(f"[{r['workspace']}] {r['doc_id']}\t{r['title']}\n    {r['snippet']}")


def _body(args):
    if getattr(args, "body_file", None):
        with open(args.body_file) as f:
            return f.read()
    return args.body or ""


def cmd_new(args):
    ws = config.resolve_one(args.workspace)
    with store.lock():
        appctl.ensure_stopped()
        doc_id = write_mod.new_doc(ws.db_path, ws.root_id, ws.server_id, ws.workspace_id,
                                   args.title, _body(args))
    print(doc_id)
    _note(f"created \"{args.title}\" in {ws.name}. Run `affine sync` to push to the server.")


def cmd_edit(args):
    if args.append_file:
        with open(args.append_file) as f:
            text = f.read()
    else:
        text = args.append or ""
    if not text.strip():
        _err("edit requires --append TEXT or --append-file FILE")
    ws = config.locate(args.doc_id, args.workspace)
    with store.lock():
        appctl.ensure_stopped()
        write_mod.append_doc(ws.db_path, args.doc_id, text)
    print(f"appended to {args.doc_id} in {ws.name}")
    _note("Run `affine sync` to push to the server.")


def cmd_delete(args):
    ws = config.locate(args.doc_id, args.workspace)
    with store.lock():
        appctl.ensure_stopped()
        write_mod.trash_doc(ws.db_path, ws.root_id, args.doc_id)
    print(f"moved {args.doc_id} to Trash in {ws.name}")
    _note("Run `affine sync` to push to the server.")


def cmd_sync(args):
    wss = [config.resolve_one(args.workspace)] if args.workspace else config.cloud_workspaces()
    with store.lock():
        ok, secs, remaining = appctl.sync(wss)
    if ok:
        print(f"synced {len(wss)} workspace(s) ({secs}s)")
    else:
        _err(f"unpushed after {secs}s in: {[w.name for w in remaining]}; run `affine sync` again")


def build_parser():
    p = argparse.ArgumentParser(prog="affine", description="Deterministic local AFFiNE doc management")
    sub = p.add_subparsers(dest="cmd", required=True)

    def ws(sp):
        sp.add_argument("--workspace", "-w", help="workspace name (substring) or id")

    def js(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    sp = sub.add_parser("workspaces", help="list workspaces")
    js(sp)
    sp.set_defaults(fn=cmd_workspaces)

    sp = sub.add_parser("list", help="list docs in a workspace")
    ws(sp); js(sp)
    sp.add_argument("--limit", type=int)
    sp.add_argument("--all", action="store_true", help="include trashed docs")
    sp.set_defaults(fn=cmd_list)

    sp = sub.add_parser("read", help="print a doc as Markdown")
    sp.add_argument("doc_id")
    ws(sp); js(sp)
    sp.set_defaults(fn=cmd_read)

    sp = sub.add_parser("search", help="keyword search across docs")
    sp.add_argument("query")
    ws(sp); js(sp)
    sp.add_argument("--limit", type=int, default=10)
    sp.set_defaults(fn=cmd_search)

    sp = sub.add_parser("new", help="create a doc (quits AFFiNE; sync separately)")
    sp.add_argument("--title", required=True)
    sp.add_argument("--body")
    sp.add_argument("--body-file")
    ws(sp)
    sp.set_defaults(fn=cmd_new)

    sp = sub.add_parser("edit", help="append Markdown to a doc (quits AFFiNE; sync separately)")
    sp.add_argument("doc_id")
    sp.add_argument("--append")
    sp.add_argument("--append-file")
    ws(sp)
    sp.set_defaults(fn=cmd_edit)

    sp = sub.add_parser("delete", help="move a doc to Trash (quits AFFiNE; sync separately)")
    sp.add_argument("doc_id")
    ws(sp)
    sp.set_defaults(fn=cmd_delete)

    sp = sub.add_parser("sync", help="relaunch AFFiNE and push local changes to the server")
    ws(sp)
    sp.set_defaults(fn=cmd_sync)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        args.fn(args)
    except (ValueError, RuntimeError, FileNotFoundError) as e:
        _err(str(e))


if __name__ == "__main__":
    main()
