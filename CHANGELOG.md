# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.3.0] — 2026-09-07

OpenClaw compatibility and a SKILL.md written for smaller models.

- `--sync` on `new`, `edit` and `delete` pushes the workspace just written to as soon as the
  write lands, so a single change is one command instead of a write followed by `affine sync`.
  Batching several writes before one `affine sync` still works as before.
- The launcher builds its venv from the first Python ≥ 3.10 it finds (`$AFFINE_PYTHON`,
  `python3` on `PATH`, Homebrew, `/usr/local`, then versioned names) and rebuilds a venv that can
  no longer import `pycrdt`. macOS's bundled Python 3.9 cannot install `pycrdt`, so a launcher
  that trusted `python3` blindly failed on a stock Mac.
- `SKILL.md` is restructured as numbered rules, task recipes with exact commands, a command
  reference, and an error table, so that a smaller local model (e.g. a 27B Qwen under OpenClaw)
  can follow it without inference. The frontmatter carries OpenClaw's `os`, `emoji` and
  `requires.bins` gating fields and a top-level `homepage`.
- README documents the OpenClaw install path (`~/.openclaw/workspace/skills/affine`).

## [0.2.0] — 2026-07-31

In-place editing. Previously `edit` could only append, so correcting a word inside a document
meant deleting and recreating it — which destroys every table, because the Markdown writer
cannot emit table blocks.

- `edit --replace OLD --with NEW` edits text in place by splicing the block's existing `Y.Text`,
  preserving block ids, inline formatting and table structure. It reaches table cells, which a
  Markdown round-trip cannot reproduce. `--with ""` deletes the matched text.
- `edit --insert-after ANCHOR` / `--insert-before ANCHOR` add Markdown blocks at a position
  instead of only at the end; `--delete-block ANCHOR` removes a block and any nested children;
  `--set-title` retitles a doc, updating the page block and the workspace root's page meta
  together so the document and the sidebar cannot disagree.
- `--dry-run` reports what would change and writes nothing. It runs against a read-only copy of
  the store, so unlike every other write path it does not quit AFFiNE.
- Replacements and anchors require exactly one match; the error lists the candidates. `--all`
  replaces every occurrence and `--in-block ANCHOR` confines a replace to a single block, for
  phrases that recur legitimately (a banned term and the rule quoting it). An anchor equal to a
  whole block's text wins over anchors merely contained in one.
- Every edit is validated against an in-memory copy before it is written: targeted fields must
  match a plain-Python computation of the expected result, all other text fields must be
  byte-identical, and the block structure must be unchanged.
- Fixed a latent corruption hazard: `Y.Text` offsets are UTF-8 byte offsets, not Python string
  indices. Codepoint indices silently mangle text containing em dashes, curly quotes or accents
  and panic the Rust layer on astral characters. All conversion is centralized in
  `affine_local/textops.py` and covered by regression tests.
- `edit --append` now runs through the same plan-validate-commit path as the other operations
  and supports `--dry-run`.

## [0.1.0] — 2026-07-23

Initial release.

- Read a self-hosted AFFiNE knowledge base from the desktop app's local store, with no server
  API, token, or password: `workspaces`, `list`, `search`, `read` (docs decoded to Markdown).
- Workspace-aware: `list`/`search` sweep every workspace and `read`/`edit`/`delete` auto-locate
  a doc in whichever workspace holds it, so callers never need to know where a doc lives.
- `read` renders `affine:table` blocks as GitHub-flavored Markdown tables (first row as header).
- Write via local Yjs deltas: `new` (create), `edit` (append Markdown), `delete` (move to
  Trash). Creates register the doc in the workspace root doc's `meta.pages`.
- `sync` drives the deterministic quit → write → relaunch → foreground recipe so the AFFiNE
  desktop app pushes staged changes to the server.
- Safety: pruned backups + `integrity_check` around every write, in-memory delta validation,
  `flock` serialization, and a storage-schema guard.
- Requires the AFFiNE desktop client ≥ v0.27.3 (fixes the cold-start session bug that blocked
  programmatic sync on 0.27.2 and earlier).
