# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

## [0.5.0] — 2026-09-13

Inline dependencies (PEP 723). No virtualenv, no install step.

**Setup change:** `uv` replaces a system Python as the one prerequisite. Existing users should
install it (`brew install uv`, or `curl -LsSf https://astral.sh/uv/install.sh | sh`); a leftover
`.venv/` in the skill folder is no longer used and can be deleted.

- `affine` is now a [PEP 723](https://peps.python.org/pep-0723/) script. It declares
  `requires-python = ">=3.10"` and its sole dependency, `pycrdt==0.14.1`, in a comment block at
  the top of the file, which is now the single source of truth for the pin. `requirements.txt`
  is removed.
- No virtualenv is created and nothing is written into the skill directory, so the folder stays
  safe to sync, back up, or commit. Previously a 20 MB `.venv/` was built in place, and its
  absolute interpreter symlink could break archive tools that require relative symlink targets.
- The interpreter search (`$AFFINE_PYTHON`, `python3`, Homebrew, `/usr/local`, versioned names)
  is gone. uv resolves a conforming Python and downloads one if the host has none, so a stock
  Mac shipping Python 3.9 needs no separate Python install.
- The per-invocation `import pycrdt` health check and venv rebuild are gone; startup drops from
  ~0.12s to ~0.05s.
- Runner-agnostic: `./affine` (uv via the shebang), `uv run --script affine`, or `python3 affine`
  where `pycrdt` is already importable. Other PEP 723 runners such as `pipx run` also work.
- `SKILL.md` gating metadata now requires `uv` rather than `python3`; skill version 0.5.0.
- README documents, in plain language, that what decides whether the skill works is *where the
  agent runs*, not which vendor it is. An agent on your own Mac works — Claude Code in the
  terminal, OpenClaw, or running `./affine` by hand — while an agent on a remote server or in a
  hosted sandbox cannot, because AFFiNE and its local store are not there.

## [0.4.0] — 2026-09-11

Tables in written content.

- `new`, `edit --insert-after/--insert-before` and `edit --append` turn a GitHub-flavored pipe
  table (header row, `| --- |` separator row, body rows) into a real `affine:table` block: one
  column per header cell, one row per line, first row as the header, `\|` as a literal pipe in a
  cell, short rows padded and long rows truncated to the header width. Column and row order use
  BlockSuite fractional-index keys (`a0`, `a1`, …), the same order `read` sorts by, so a table
  round-trips through `new` and `read` unchanged.
- Insert and append validation covers every table cell, so a table that failed to land is
  rejected before anything is written.

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
