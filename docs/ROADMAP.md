# Roadmap

Where the packaging and the tool are headed. Status as of 2026-10-22 —
items move to "Done" as they land; the deferred ScriptEngine
capabilities live in [SUMMARY.md](SUMMARY.md) (rows with status
`possible`) and are tracked per-domain there.

## Agent handoff (picking this up)

You are most likely an AI agent continuing this work. Read, in order:
[SCRIPTENGINE_EXPLORATION.md](SCRIPTENGINE_EXPLORATION.md) (how to
discover and verify), [SCRIPTENGINE_GOTCHAS.md](SCRIPTENGINE_GOTCHAS.md)
(trap table — every `G-nn` cited in code and docs refers here),
[SUMMARY.md](SUMMARY.md) (done vs possible),
[TESTING.md](TESTING.md) (live results — the authority over any doc
claim).

The open work, in priority order:

1. **Live-test pass** for every row marked `PENDING` in TESTING.md —
   read-only first; destructive/download runs only when the owner
   explicitly asks (they do answer GUI dialogs during armed sessions).
   Append one row per run; if reality contradicts a doc claim, fix the
   claim in the same commit.
2. **Implement the 🟡 `possible` rows in SUMMARY.md**, nearest-term
   first: in-project Library Manager
   (`app.get_library_manager()` → `add_library` / `references` /
   `set_redirection` — ScriptLibManObject stub), then device-tree
   authoring, user management, text lists / image pools / external
   files (every row carries its stub chain).
3. **ROADMAP "Next" items** below.

Non-negotiable discipline:

- Atomic commits per domain; gates before every commit (pytest, flake8
  strict pair, `python -m build`, `mkdocs build --strict` when docs
  changed).
- The docs are a machine-readable contract: any code change updates
  SCRIPTENGINE_GOTCHAS.md (new `G-nn` rows), SUMMARY.md (status flips),
  SCRIPTENGINE_API.md and TESTING.md (one row per live run) **in the
  same commit**.
- Never import `relay.py` under CPython (it runs the poll loop at
  module level); executor imports need `ECP_NO_RUN=1`.
- Destructive/PLC-download live tests only on explicit owner request.
- The ScriptLib stubs and decompiled CHM stay local and out of the
  repo — document API facts in your own words (license findings in
  EXPLORATION).
- Server restarts are allowed at will (owner granted). Daemon restart
  is required for server-side tool changes; executor changes go live on
  re-exec without re-arming.

## Done

- Packaging: src-layout package, `__version__` single-sourcing, console
  entry point, sdist+wheel build in CI, wheel install + entry-point
  smoke, license/notice metadata (Apache-2.0).
- Quality gates: strict flake8 fail-fast, offline test suite (15 tests;
  fake `ILibManager` for the library commands), build gate, dead-tool
  absence pins.
- Docs contract: machine-readable `SCRIPTENGINE_*` suite
  (API reference, GOTCHAS with stable IDs, NOT_EXPOSED inventory,
  EXPLORATION how-to, SUMMARY done+possible master table), TESTING.md
  live-result tracking, mkdocs-material site with the architecture
  diagram.
- Tool surface: 43 tools across build/import/export, online
  read/write/prepared values, login with online-change control
  (`OnlineChangeOption` incl. stub-only `Keep`), PLC file system via
  `online.create_online_device()`, source archive push/pull, library
  repository curation, project tree + ST authoring, task configuration,
  symbol configuration, device scan/communication/parameters,
  application and project lifecycle. Read-only login (`Keep`), headless
  prompt hardening, and the confirm-gate policy with
  `destructiveHint`/`readOnlyHint` annotations.
- Live-verified: export (file + string forms), login ladder (Try, 15 s),
  plc_ls (directory fallback), prepared_values, libs/repos/find,
  tree/task_config/device_scan/project info.

## Next

- **mkdocs CI job**: build the site on every PR (content is in-repo;
  the site itself is generated, not committed).
- **`ecockpit-mcp install-scripts <dir>`**: write `relay.py` +
  `executor.py` (plus a starter `.env`) to an arming directory, so the
  wheel alone is enough to arm e!COCKPIT.
- **Live-test pass for the ScriptLib round**: login ladder (Keep for
  read-only, Try for deploy), object_create/textual_write,
  task_create, symbol_config_variables/create, device_scan scan/address,
  device_params read/write, project lifecycle — read-only first, then
  owner-authorized destructive checks; record every result in
  TESTING.md.
- **source_download**: retry the device `download_source` path once the
  Archive.prj staging lock clears (environmental; see TESTING.md).

## Later

- **Quality gates**: ruff (lint+format), mypy/pyright on the CPython
  half, coverage gate on the host half, pre-commit hooks; extend the
  ScriptEngine fake to the new domains (tree, task, symbol, device
  params) so destructive paths get offline coverage too.
- **PyPI publication** via trusted publishing (OIDC); `pipx install
  ecockpit-mcp`; tag-triggered release workflow with provenance.
- **MCP-protocol maturity**: resources (project tree, messages),
  prompts (diagnose-failed-build), progress notifications for long
  operations instead of blocking HTTP calls, MCP-spec conformance checks.
- **Security page**: bearer token, loopback default, confirm-gated
  destructive ops, online-commands-can-deploy caution, no credentials
  in logs.
- **Deferred ScriptEngine capabilities**: everything in SUMMARY.md with
  status `possible` — in-project library references, device-tree
  authoring (add/plug/update), user management (project + device),
  text lists, image pools, external files, IO-mapping CSV, license
  activation, gateway add/remove, project compare, compiled libraries,
  project settings.

## What stays out of scope

Re-creating e!COCKPIT functionality, CODESYS V3.5 support (another MCP
project targets that platform), and any affiliation with WAGO (see
NOTICE) are non-goals.
