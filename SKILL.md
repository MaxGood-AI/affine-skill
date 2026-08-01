---
name: affine
description: Read, search, create, edit in place, and delete documents in a self-hosted AFFiNE knowledge base by operating on the AFFiNE desktop app's local store — no server credentials or API tokens required. Use when the user wants to look something up in AFFiNE, gather information from AFFiNE docs, create/write/append an AFFiNE document, change text inside an existing one (including inside tables), retitle it, or delete/trash one. Triggers include "in AFFiNE", "our AFFiNE", "the AFFiNE wiki / knowledge base".
license: BSD-3-Clause
compatibility: macOS with the AFFiNE desktop app (>= v0.27.3) installed and signed in, plus python3. Operates directly on the local AFFiNE SQLite store; the desktop app syncs changes to the server. No API keys or environment variables.
metadata:
  version: "0.2.0"
  author: Mishkin Berteig
  openclaw:
    requires:
      bins:
        - python3
    homepage: https://github.com/MaxGood-AI/affine-skill
---

# affine

Manage documents in a **self-hosted [AFFiNE](https://affine.pro)** knowledge base without
any server API, token, or password. This skill reads and writes the AFFiNE **desktop app's
own local store** (an unencrypted SQLite database of Yjs/BlockSuite CRDT documents), decoding
docs to Markdown for reading and staging Yjs updates for writing. The desktop app then syncs
your changes to the server the same way it syncs your own edits.

Everything runs through one launcher, which bootstraps its Python virtualenv on first use:

```
<skill-dir>/affine <command> [args]
```

(When installed as a skill, `<skill-dir>` is the skill's own directory, e.g.
`~/.claude/skills/affine/affine`.) Add `--json` to any command for machine-readable output.

## Workspace-aware by default

A server can hold **many workspaces**, and a document you're looking for may be in any of
them. So `list`, `search`, `read`, `edit`, and `delete` operate across **all workspaces** by
default — you never need to know which workspace holds a doc. `read`/`edit`/`delete` **locate**
the doc automatically wherever it lives. Pass `--workspace NAME` (a case-insensitive substring
of the workspace name) to restrict any command to one workspace. Only `new` needs a target
workspace (it must pick a home to create in).

## Reading — safe anytime, never touches the app

| Command | Purpose |
|---|---|
| `affine workspaces` | List workspaces (kind, id, name). |
| `affine list [--limit N] [--all]` | List docs in **every** workspace, grouped, as `id ⇥ title`. `--limit` is per-workspace; `--all` includes trashed docs. |
| `affine search "QUERY" [--limit N]` | Keyword search across **all** workspaces → `[workspace] id ⇥ title` + snippet. |
| `affine read DOC_ID` | Auto-locate the doc across workspaces and print it as Markdown. |

To answer a question from the knowledge base: `search` the relevant terms (it sweeps every
workspace), then `read` the top hits by id. The `[workspace]` tag on each search hit tells you
where it lives, but you don't need it for `read`/`edit`/`delete` — they find the doc for you.

## Writing — a two-step workflow you MUST follow

Write commands (`new`, `edit`, `delete`) **quit the AFFiNE app** and stage the change in the
local store. They do **not** reach the server by themselves. After all your writes, run
**`affine sync` exactly once** to push everything.

| Command | Purpose |
|---|---|
| `affine new --title "T" [--body "MD" \| --body-file FILE]` | Create a doc in the target workspace (default, or `--workspace`); prints the new id. |
| `affine edit DOC_ID <operation>` | Change an existing doc in place — see **Editing** below. |
| `affine delete DOC_ID` | Move a doc to Trash (auto-located; recoverable in the AFFiNE UI). |
| `affine sync` | Relaunch AFFiNE and push staged changes in **all** cloud workspaces (~5–15s). |

Canonical pattern — do the writes, then sync once:

```
affine new --title "Q3 Notes" --body-file /tmp/notes.md
affine edit KIYQGEFu0zcfTIiGBV1VE --replace "Q2 target" --with "Q3 target"
affine edit KIYQGEFu0zcfTIiGBV1VE --append "New section text."
affine delete pzqwFDoWpcj8mv9osqF3b
affine sync
```

Until `sync` runs, changes are local-only (they would also sync next time the user opens
AFFiNE). `sync` briefly **foregrounds** the AFFiNE window — that is required to trigger the
push — and waits until nothing is unpushed.

Body/append/insert text is **Markdown**: `#`..`######` headings, `-`/`1.`/`- [ ]` lists, `>`
blockquotes, ```` ``` ```` fenced code, `---` rules, and paragraphs (one line = one
paragraph). Pass multi-line Markdown via `--body-file` / `--append-file` / `--text-file`,
not inline.

## Editing — one operation per `affine edit` call

| Operation | Purpose |
|---|---|
| `--replace "OLD" --with "NEW"` | Replace literal text wherever it lives, **including inside table cells**. Requires exactly one match unless `--all`. `--with ""` deletes the matched text. |
| `--insert-after "ANCHOR" --text "MD"` | Insert new Markdown blocks straight after the block containing ANCHOR (`--insert-before` for the other side). |
| `--delete-block "ANCHOR"` | Delete the block containing ANCHOR, plus any blocks nested under it. |
| `--set-title "TITLE"` | Retitle the doc — updates the page **and** the workspace sidebar together. |
| `--append "MD"` | Add Markdown to the end of the doc. |

Modifiers: `--all` (replace every occurrence), `--in-block "ANCHOR"` (confine a replace to one
block), `--dry-run`, and the `--with-file` / `--text-file` / `--append-file` file variants.

**Prefer `--replace` over rewriting a doc.** Replacement splices the existing rich-text object,
so block identity, inline formatting and table structure survive. Deleting and recreating a doc
to change a few words destroys every table in it, because writes can only emit paragraphs,
headings, lists, quotes, code and dividers.

### Matching rules

- **A match cannot span blocks.** Every paragraph, heading, list item and table cell is a
  separate rich-text object. `--replace` searches inside one block at a time, so an OLD string
  containing a paragraph break will never match. Change one block at a time.
- **Ambiguity is an error, not a guess.** `--replace` and every ANCHOR demand exactly one match.
  When several match, the error lists the candidates; respond by extending the search text,
  scoping with `--in-block`, or passing `--all` when you really do mean all of them.
- **An anchor that *is* a whole block beats one merely contained in a block**, so a short anchor
  can name a table-of-contents entry without colliding with the heading that repeats it.
- `--in-block` is the tool for a phrase that legitimately recurs — a term and the style rule
  quoting it, say. Scope the edit to the block you mean.

### Always dry-run a replace first

```
affine edit DOC_ID --replace "Taster" --with "Tester" --all --dry-run
```

`--dry-run` prints a before/after line per match and writes nothing. It is **read-only and does
not quit AFFiNE**, so it is free to run at any time. Use it to confirm you are not about to
rewrite a deliberate quotation of the very text you are replacing — style guides and policy docs
routinely quote the wording they ban.

## Which workspace `new` and `sync` use

Reads and doc-id operations span all workspaces (above). Two commands are workspace-specific:

- **`new`** creates in the `default_workspace` from the skill's `config.json` (copy it from
  `config.example.json`); if unset and there is exactly one cloud workspace, that one is used;
  otherwise pass `--workspace NAME` (or a workspace id).
- **`sync`** pushes **every** cloud workspace with staged changes. Because AFFiNE only syncs
  the foreground workspace on its own, `sync` opens any non-foreground workspace that still has
  unpushed changes in a tab (via the `affine://` deep link) so it, too, pushes. This may leave
  an extra tab or two open in the app — harmless.

## Safety & guarantees

- Every write is preceded by a **pruned backup** of the store (`storage.db.skillbak-*`) and
  followed by `PRAGMA integrity_check`; the delta is validated in memory before it is written.
- Edits are **validated exhaustively before they are written**: the change is applied to an
  in-memory copy and the result must match, field by field, what was asked for — every
  untargeted paragraph, list item and table cell has to come back byte-identical, and the block
  structure has to be unchanged. Anything else aborts without touching the store.
- Operations are **`flock`-serialized**, so concurrent agents don't collide.
- A **schema check** aborts if the AFFiNE storage format differs from what this skill supports.
- `delete` moves to **Trash** (recoverable); it never permanently erases. `--delete-block`
  removes content from a doc and is **not** recoverable through the UI's Trash — dry-run it.

## Requirements & limitations

- Requires the **AFFiNE desktop app ≥ v0.27.3** installed and **signed in** — `sync` relies on
  the app's session to reach the server. (v0.27.2 and earlier have a cold-start bug that
  re-logs-in every launch and blocks programmatic sync; upgrade first.)
- macOS only (uses `osascript`/`open` for the app lifecycle). The read/decode/encode core is
  portable; only `affine_local/appctl.py` is macOS-specific.
- **Tables** (`affine:table`) render as GitHub-flavored Markdown tables on read (first row is
  treated as the header). Inline text formatting (bold/italic/links), database blocks, and
  edgeless-canvas elements still render as plain text or placeholders.
- **New** content is limited to paragraphs, headings, lists, quotes, code and dividers — the
  block types the Markdown writer emits. `--replace` has no such limit: it edits the text of
  whatever block already exists, which is how table cells get corrected.
- `--replace` matches **literal text, not patterns** — no regular expressions or wildcards.
