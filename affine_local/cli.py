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
    if not wss:
        _err("no AFFiNE workspaces found (is the desktop app installed and signed in?)")
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


def _sync(wss):
    """Relaunch AFFiNE and wait until every given cloud workspace has pushed."""
    with store.lock():
        ok, secs, remaining = appctl.sync(wss)
    if ok:
        print(f"synced {len(wss)} workspace(s) ({secs}s)")
    else:
        _err(f"unpushed after {secs}s in: {[w.name for w in remaining]}; run `affine sync` again")


def _after_write(ws, sync_now):
    """Push the workspace just written to when --sync was given; otherwise remind."""
    if not sync_now:
        _note("Run `affine sync` to push to the server.")
    elif ws.kind != "cloud":
        _note(f"{ws.name} is a local workspace; nothing to push.")
    else:
        _sync([ws])


def cmd_new(args):
    ws = config.resolve_one(args.workspace)
    with store.lock():
        appctl.ensure_stopped()
        doc_id = write_mod.new_doc(ws.db_path, ws.root_id, ws.server_id, ws.workspace_id,
                                   args.title, _body(args))
    print(doc_id)
    _note(f"created \"{args.title}\" in {ws.name}.")
    _after_write(ws, args.sync)


def _text_arg(args, inline, file_attr):
    path = getattr(args, file_attr, None)
    if path:
        with open(path) as f:
            return f.read()
    return inline


def _plan_edit(args, base, root, ws):
    """Build the Change for whichever edit operation was requested."""
    doc_id = args.doc_id
    if args.replace is not None:
        new = _text_arg(args, args.with_text, "with_file")
        if new is None:
            _err("--replace requires --with TEXT or --with-file FILE (use --with '' to delete)")
        return write_mod.plan_replace(base, doc_id, args.replace, new, args.all,
                                      in_block=args.in_block)
    if args.insert_after is not None or args.insert_before is not None:
        md = _text_arg(args, args.text, "text_file")
        if not (md or "").strip():
            _err("--insert-after/--insert-before requires --text MD or --text-file FILE")
        before = args.insert_before is not None
        anchor = args.insert_before if before else args.insert_after
        return write_mod.plan_insert(base, doc_id, anchor, md, before=before)
    if args.delete_block is not None:
        return write_mod.plan_delete_block(base, doc_id, args.delete_block)
    if args.set_title is not None:
        return write_mod.plan_set_title(base, doc_id, root, ws.root_id, args.set_title)
    md = _text_arg(args, args.append, "append_file")
    if not (md or "").strip():
        _err("edit requires one of --append, --replace, --insert-after, "
             "--insert-before, --delete-block or --set-title")
    return write_mod.plan_append(base, doc_id, md)


def cmd_edit(args):
    ws = config.locate(args.doc_id, args.workspace)
    needs_root = args.set_title is not None

    if args.dry_run:
        # Planning is read-only, so a dry run never has to quit the app.
        with store.read_conn(ws.db_path) as con:
            store.check_schema(con)
            base = store.load_doc(con, args.doc_id)
            root = store.load_doc(con, ws.root_id) if needs_root else None
        change = _plan_edit(args, base, root, ws)
        _note(f"(dry run — nothing written; workspace: {ws.name})")
        for line in change.summary:
            print(line)
        return

    with store.lock():
        appctl.ensure_stopped()
        with store.write_conn(ws.db_path) as con:
            store.check_schema(con)
            base = store.load_doc(con, args.doc_id)
            root = store.load_doc(con, ws.root_id) if needs_root else None
        change = _plan_edit(args, base, root, ws)
        write_mod.commit(ws.db_path, change)
    for line in change.summary:
        print(line)
    print(f"edited {args.doc_id} in {ws.name}")
    _after_write(ws, args.sync)


def cmd_delete(args):
    ws = config.locate(args.doc_id, args.workspace)
    with store.lock():
        appctl.ensure_stopped()
        write_mod.trash_doc(ws.db_path, ws.root_id, args.doc_id)
    print(f"moved {args.doc_id} to Trash in {ws.name}")
    _after_write(ws, args.sync)


def cmd_sync(args):
    wss = [config.resolve_one(args.workspace)] if args.workspace else config.cloud_workspaces()
    _sync(wss)


def build_parser():
    p = argparse.ArgumentParser(prog="affine", description="Deterministic local AFFiNE doc management")
    sub = p.add_subparsers(dest="cmd", required=True)

    def ws(sp):
        sp.add_argument("--workspace", "-w", help="workspace name (substring) or id")

    def js(sp):
        sp.add_argument("--json", action="store_true", help="machine-readable output")

    def sync_flag(sp):
        sp.add_argument("--sync", action="store_true",
                        help="push to the server right after this write (same as running "
                             "`affine sync` afterwards)")

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
    ws(sp); sync_flag(sp)
    sp.set_defaults(fn=cmd_new)

    sp = sub.add_parser(
        "edit",
        help="edit a doc in place: replace/insert/delete-block/set-title/append "
             "(quits AFFiNE; sync separately)",
    )
    sp.add_argument("doc_id")
    op = sp.add_mutually_exclusive_group()
    op.add_argument("--replace", metavar="OLD",
                    help="literal text to replace in place (must match exactly one "
                         "block unless --all); works inside table cells")
    op.add_argument("--insert-after", metavar="ANCHOR",
                    help="insert --text after the block containing ANCHOR")
    op.add_argument("--insert-before", metavar="ANCHOR",
                    help="insert --text before the block containing ANCHOR")
    op.add_argument("--delete-block", metavar="ANCHOR",
                    help="delete the block containing ANCHOR (and any nested children)")
    op.add_argument("--set-title", metavar="TITLE",
                    help="retitle the doc (updates the page and the workspace sidebar)")
    op.add_argument("--append", metavar="MD", help="append Markdown to the end of the doc")
    sp.add_argument("--with", dest="with_text", metavar="NEW",
                    help="replacement text for --replace (use '' to delete the match)")
    sp.add_argument("--with-file", metavar="FILE", help="read --with text from a file")
    sp.add_argument("--text", metavar="MD", help="Markdown for --insert-after/--insert-before")
    sp.add_argument("--text-file", metavar="FILE", help="read --text from a file")
    sp.add_argument("--append-file", metavar="FILE", help="read --append text from a file")
    sp.add_argument("--all", action="store_true",
                    help="with --replace: replace every occurrence instead of requiring one")
    sp.add_argument("--in-block", metavar="ANCHOR",
                    help="with --replace: only inside the block containing ANCHOR")
    sp.add_argument("--dry-run", action="store_true",
                    help="show what would change without writing (leaves AFFiNE running)")
    ws(sp); sync_flag(sp)
    sp.set_defaults(fn=cmd_edit)

    sp = sub.add_parser("delete", help="move a doc to Trash (quits AFFiNE; sync separately)")
    sp.add_argument("doc_id")
    ws(sp); sync_flag(sp)
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
