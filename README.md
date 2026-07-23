# affine-skill

Deterministic, near-zero-dependency access to a **self-hosted [AFFiNE](https://affine.pro)**
knowledge base for AI agents and humans — with **no server API, token, or password**. It
works by operating directly on the AFFiNE **desktop app's local store**: reads decode
AFFiNE's Yjs/BlockSuite CRDT documents to Markdown; writes stage Yjs updates locally, and the
AFFiNE app pushes them to your server the same way it syncs your own edits.

- **Author:** Mishkin Berteig
- **License:** BSD-3-Clause (see `LICENSE.txt`)
- **Copyright:** © 2026 14334876 Canada Inc. (dba. MaxGood.work)

It ships as a [Claude](https://claude.com/claude-code) / [OpenClaw](https://clawhub.ai) skill
(`SKILL.md` carries the skill + ClawHub frontmatter) and works as a plain CLI.

## Requirements

- macOS with the **AFFiNE desktop app ≥ v0.27.3** installed and **signed in**.
  v0.27.2 and earlier have a client cold-start bug that re-logs-in every launch and prevents
  programmatic sync — upgrade first.
- `python3`. The `affine` launcher creates its own `.venv` on first run and installs the sole
  dependency, [`pycrdt`](https://pypi.org/project/pycrdt/) (Yjs/CRDT decode + encode).

## Install

> **New here, or not a developer?** Follow **[DETAILED_SETUP.md](DETAILED_SETUP.md)** — a complete,
> step-by-step, non-technical guide that takes you from nothing to a working setup on a Mac. The
> steps below are the short version for developers.

Clone the repo, then either run `./affine …` directly or register it as a skill by symlinking
it into your skills directory:

```
git clone https://github.com/MaxGood-AI/affine-skill.git
ln -s "$PWD/affine-skill" ~/.claude/skills/affine
```

Copy `config.example.json` to `config.json` and set your default workspace (optional).

## Usage

```
./affine workspaces                 # list workspaces
./affine list [--limit N] [--all]   # list docs in EVERY workspace, grouped
./affine search "QUERY"             # keyword search across ALL workspaces → [workspace] id, title, snippet
./affine read DOC_ID                # auto-locate the doc in any workspace, print as Markdown
./affine new --title "T" --body-file body.md   # create in the default/--workspace target
./affine edit DOC_ID --append "text"           # auto-located
./affine delete DOC_ID              # move to Trash (auto-located)
./affine sync                       # push staged writes in ALL cloud workspaces
```

Add `--json` for machine output. **Workspace-aware:** a server can hold many workspaces, so
`list`/`search`/`read`/`edit`/`delete` span all of them and `read`/`edit`/`delete` locate a
doc wherever it lives — you never need to know which workspace holds it. Pass `--workspace
NAME` to restrict any command; only `new` requires a single target (default or `--workspace`).

### The write → sync workflow

Write commands (`new`/`edit`/`delete`) **quit AFFiNE** and stage the change locally; they do
not reach the server on their own. Do all writes, then run **`affine sync` once** — it
relaunches and briefly foregrounds AFFiNE, whose session pushes the changes (~5s). Until then
changes are local-only (they also sync next time you open AFFiNE).

## How it works

- **Store:** `~/Library/Application Support/AFFiNE/workspaces/<server>/<workspace>/storage.db`
  — a doc is its `snapshots` row plus newer `updates` rows (Yjs v1), merged with `pycrdt`.
- **Read:** merge + walk the BlockSuite `blocks` map → Markdown. Never touches the app.
- **Write:** build a Yjs delta, validate it in memory, back up the DB, insert an `updates` row
  and bump `clocks`. Creates also register the doc in the root doc's `meta.pages`; the app
  self-heals the `spaces` subdoc on next launch. Deletes set the `trash` flag.
- **Sync:** quit → write → relaunch → foreground. AFFiNE's sync engine re-scans on launch and
  pushes any doc whose local clock is ahead of the pushed clock. (A resident, connected app
  won't re-scan on its own — the relaunch + foreground is the trigger.) AFFiNE only syncs the
  **foreground** workspace, so `sync` opens any other workspace with pending changes in a tab
  via the `affine://` deep link, ensuring every cloud workspace pushes.

Every write is preceded by a pruned backup (`storage.db.skillbak-*`) and followed by
`PRAGMA integrity_check`; operations are `flock`-serialized; a schema check aborts if the
AFFiNE storage format changes. `delete` is recoverable (Trash), never a hard erase.

## Layout

```
affine            launcher (ensures venv, dispatches)
affine_local/     store, decode, blocks, write, search, appctl, config, cli
tests/            unit tests (pure functions + in-memory Yjs)
SKILL.md          skill manifest + ClawHub frontmatter (agent-facing docs)
config.example.json  copy to config.json to set a default workspace
```

Run tests: `PYTHONPATH=. ./.venv/bin/python -m unittest discover -s tests`

## Compatibility & scope

macOS only for the app lifecycle (`osascript`/`open`); the decode/encode core is portable.
Tables (`affine:table`) render as Markdown tables on read. Inline formatting
(bold/italic/links), database blocks, and edgeless-canvas elements render as plain text or
placeholders on read and are not produced on write in this version. Tested against AFFiNE
self-hosted 0.27 with desktop client 0.27.3.
