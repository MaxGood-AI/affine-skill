# Changelog

All notable changes to this project are documented here. This project adheres to
[Semantic Versioning](https://semver.org/).

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
