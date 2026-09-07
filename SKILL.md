---
name: affine
description: Search, read, create, edit, and delete documents in the team's self-hosted AFFiNE knowledge base (the wiki) through the AFFiNE desktop app's local store — no server credentials or API tokens. Use for any request that mentions AFFiNE, "our wiki", "the knowledge base", brand guidelines, or looking up / writing team documentation.
license: BSD-3-Clause
homepage: https://github.com/MaxGood-AI/affine-skill
compatibility: macOS with the AFFiNE desktop app (>= v0.27.3) installed and signed in, plus Python >= 3.10. Operates directly on the local AFFiNE SQLite store; the desktop app syncs changes to the server. No API keys or environment variables.
metadata:
  version: "0.3.0"
  author: Mishkin Berteig
  openclaw:
    emoji: "📚"
    os:
      - darwin
    requires:
      bins:
        - python3
    homepage: https://github.com/MaxGood-AI/affine-skill
---

# affine — the team's AFFiNE knowledge base

AFFiNE is the team's wiki. This skill reads and writes it through one command-line program
named `affine`, which sits in the **same folder as this SKILL.md**. Always call it by its full
path. Examples:

- OpenClaw: `~/.openclaw/workspace/skills/affine/affine`
- Claude Code: `~/.claude/skills/affine/affine`

No login, token, or API key is needed. The first run installs a small Python helper (up to a
minute). Every command prints plain text; add `--json` to any command for JSON output.

## Rules — read these first

1. Put every argument in double quotes: `affine search "brand colours"`.
2. Multi-line text never goes on the command line. Write it to a file (for example
   `/tmp/body.md`) and pass the file with `--body-file`, `--append-file`, `--text-file` or
   `--with-file`.
3. Reading (`workspaces`, `list`, `search`, `read`, and any command with `--dry-run`) is always
   safe. It never disturbs the AFFiNE app.
4. Writing (`new`, `edit`, `delete`) quits the AFFiNE app, stages the change locally, and does
   **not** reach the server by itself. Add `--sync` to the write command to push right away, or
   run `affine sync` once after several writes. Until then the change is local-only.
5. Before any `--replace` or `--delete-block`, run the same command with `--dry-run` first and
   read the before/after lines it prints. Only then run it without `--dry-run`.
6. One editing operation per `affine edit` command. To make three changes, run `affine edit`
   three times.
7. Never open, copy, or modify AFFiNE's `storage.db` yourself. Only use the `affine` command.
8. Document ids look like `KIYQGEFu0zcfTIiGBV1VE` (21 characters). Get them from `search` or
   `list`. Never guess an id.

## Recipes

### Answer a question from the knowledge base

1. `affine search "the key words"` — prints one hit per line as `[workspace] DOC_ID  Title`,
   followed by a snippet. It sweeps every workspace.
2. `affine read DOC_ID` for the best one or two hits — prints the document as Markdown.
3. Answer from what you read. If nothing matched, search again with fewer or different words.

### See what exists

- `affine list` — every document in every workspace, one `DOC_ID  Title` per line.
- `affine workspaces` — the workspaces (kind, id, name).

### Create a document

1. Write the body as Markdown to a file, for example `/tmp/body.md`. Supported: `#` headings,
   `-` / `1.` / `- [ ]` lists, `>` quotes, fenced code, `---` rules, paragraphs (one line = one
   paragraph).
2. `affine new --title "Meeting Notes 2026-09-07" --body-file /tmp/body.md --sync`
3. Output: the new DOC_ID, then `synced 1 workspace(s)`.

The document is created in the default workspace named in the skill's `config.json`. Add
`--workspace "Name"` to create it somewhere else.

### Change wording inside a document (works inside table cells too)

1. `affine edit DOC_ID --replace "old wording" --with "new wording" --dry-run`
2. Check that the printed before/after line is exactly the change you want.
3. Run the same command without `--dry-run` and with `--sync`.

- `--with ""` deletes the matched text.
- Error `no match`: copy the exact text from `affine read DOC_ID` and try again.
- Error listing several matches: use a longer, more specific text; or add
  `--in-block "text found only in the right block"`; or add `--all` when every occurrence
  really should change.

### Add content to a document

- At the end: `affine edit DOC_ID --append-file /tmp/more.md --sync`
- After a specific block: `affine edit DOC_ID --insert-after "text in that block" --text-file /tmp/more.md --sync`
- Before a specific block: same with `--insert-before`.

### Rename a document

`affine edit DOC_ID --set-title "New Title" --sync` — updates the page and the sidebar together.

### Remove one block (paragraph, heading, list item)

1. `affine edit DOC_ID --delete-block "text in that block" --dry-run`
2. If the printed block is the right one, run it again without `--dry-run` and with `--sync`.

This removes the block and anything nested under it. It cannot be undone from AFFiNE's Trash.

### Delete a whole document

`affine delete DOC_ID --sync` — moves it to AFFiNE's Trash, where the user can restore it.

### Push pending changes

`affine sync` — relaunches AFFiNE, brings its window to the front for a few seconds, and
waits until every cloud workspace has pushed (usually 5–15 s). The window flash is expected.
`sync` may leave an extra workspace tab open in the app; that is harmless.

## Command reference

| Command | What it does |
|---|---|
| `affine workspaces` | List workspaces. |
| `affine list [--limit N] [--all]` | List docs in every workspace. `--all` includes trashed docs. |
| `affine search "QUERY" [--limit N]` | Keyword search across every workspace. |
| `affine read DOC_ID` | Print a doc as Markdown (auto-located in any workspace). |
| `affine new --title "T" --body-file FILE [--workspace "W"] [--sync]` | Create a doc; prints its id. `--body "text"` works for one-line bodies. |
| `affine edit DOC_ID --replace "OLD" --with "NEW" [--all] [--in-block "A"] [--dry-run] [--sync]` | Replace literal text in place. |
| `affine edit DOC_ID --insert-after "A" --text-file FILE [--dry-run] [--sync]` | Insert Markdown after the block containing A (`--insert-before` for before). `--text "MD"` for one line. |
| `affine edit DOC_ID --delete-block "A" [--dry-run] [--sync]` | Delete the block containing A and its children. |
| `affine edit DOC_ID --set-title "T" [--sync]` | Retitle a doc. |
| `affine edit DOC_ID --append-file FILE [--sync]` | Append Markdown at the end. `--append "MD"` for one line. |
| `affine delete DOC_ID [--sync]` | Move a doc to Trash. |
| `affine sync` | Push every staged change to the server. |

Every command accepts `--workspace "NAME"` (a case-insensitive part of the workspace name) to
limit it to one workspace, and `--json` for machine-readable output.

## How text matching works

- Matches are **literal text**. No regular expressions, no wildcards.
- A match **cannot span two blocks**. Every paragraph, heading, list item and table cell is a
  separate block. Change one block at a time.
- `--replace` and every anchor (`--insert-after`, `--insert-before`, `--delete-block`,
  `--in-block`) must match **exactly one** block. When several match, the error lists them:
  respond with a longer text, `--in-block`, or (for replace only) `--all`.
- An anchor that equals a whole block's text beats one that is merely contained in a block.
- Prefer `--replace` over deleting and recreating a document. Replacement keeps block ids,
  inline formatting and tables intact; recreating destroys every table.

## Errors and what to do

| Message | Meaning and action |
|---|---|
| `no AFFiNE workspaces found` | AFFiNE desktop is not installed or not signed in on this Mac. Tell the user; nothing else works until it is. |
| `doc X not found in any workspace` | Wrong id. Run `affine search` or `affine list` and copy the id. |
| `no match for ...` / several matches listed | See "How text matching works". |
| `multiple workspaces — pass --workspace` | `new` needs a target: add `--workspace "Name"` (names from `affine workspaces`). |
| `AFFiNE is still running and would not quit` | Ask the user to quit AFFiNE, then retry the write. |
| `unpushed after 120s ...` | Run `affine sync` again. If it repeats, tell the user AFFiNE may be signed out. |
| `affine needs Python >= 3.10` | Tell the user to run `brew install python`. |

## Safety and limits

- Every write backs up the store first (`storage.db.skillbak-*`), validates the change in
  memory, writes it, then runs an integrity check. Operations are lock-serialized, and a schema
  check aborts if the AFFiNE storage format is not the supported one.
- `delete` moves to Trash and is recoverable in the app. `--delete-block` is not.
- Reads render tables as Markdown tables. Inline bold/italic/links come out as plain text;
  database blocks and canvas elements come out as placeholders.
- New content is limited to paragraphs, headings, lists, quotes, code and dividers. `--replace`
  edits the text of any existing block, including table cells, so tables are corrected in place.
- Requires the AFFiNE desktop app ≥ v0.27.3, installed and signed in. macOS only.
