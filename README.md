# ecockpit-mcp

MCP (Model Context Protocol) server exposing e!COCKPIT PLC automation to
LLM/AI editors: build, import, compile, online variable read, project
snapshot, live-PLC download — as typed, schema-discoverable tools.

Self-contained: everything that runs on the e!COCKPIT machine ships in this
repo. The only external input is YOUR automation project (pointed at by
`ECP_PROJECT_ROOT`).

> **Disclaimer** — independent tooling, not affiliated with, endorsed by, or
> sponsored by WAGO GmbH & Co. KG. "e!COCKPIT" and "WAGO" are trademarks of
> WAGO GmbH & Co. KG, used here solely to identify the platform this tooling
> operates on. WAGO has discontinued e!COCKPIT; this project targets
> existing installations.

## Architecture

```
MCP client (ZCode etc.)
    |  streamable-http  http://<host>:9777/mcp  (bearer token; default)
    |  or stdio         (--transport stdio, client-spawned)
    v
src/ecockpit_mcp/server.py        <- host Python 3.10+ (this repo)
    |  FILE PROTOCOL (local to the e!COCKPIT machine)
    |  tmp/ecockpit_cmd.json + _cmd_result.json + _watch.heartbeat
    v
src/ecockpit_mcp/relay.py         <- IronPython INSIDE e!COCKPIT (this repo;
    |                               arm once per session: FILE -> Scripting
    |                               -> Execute script file)
    v  exec per command
src/ecockpit_mcp/executor.py      <- command implementations (this repo;
                                    drives the open project via the
                                    e!COCKPIT ScriptEngine)
```

All three parts read ONE `.env` found next to the scripts (the search walks
up from the script's own directory — never the cwd — so it also finds a
repo-root `.env` in a checkout). The work dir holding the file-protocol
files and logs defaults to `tmp/` next to the scripts (`ECP_WORK_DIR`
overrides).

The file protocol is intentionally simple: the in-instance side is
single-threaded IronPython inside a GUI, so transport latency is irrelevant
next to 15-60 s command durations, and atomic-rename files are crash-safe
and debuggable (`cat tmp/ecockpit_cmd.json`).

## Your automation project

`ECP_PROJECT_ROOT` points at your automation project repo. The executor
expects:

- `plcopensrc/` — exported PLCopen XML sources (`Application.*.xml`),
  auto-discovered one level deep (`plcopensrc/<device>/` works too);
  override with `ECP_XML_DIR`
- `.ecp` project file (optional — one under the project root is
  auto-discovered; otherwise the already-open project in e!COCKPIT is used)
- optional `.env` keys in THIS repo's `.env`: `REMOVED_POUS` (stale-object
  cleanup list), `IGNORE_CODES` (known-noise compiler codes),
  `PLC_USER`/`PLC_PASS` (PLC login for online commands),
  `ECP_VERSION_POU` (POU whose `sAppVersion` constant names snapshots)

There is **no built-in default** for `ECP_PROJECT_ROOT` — the server and
executor are install-agnostic on purpose.

## Quickstart

Prereqs: Windows with e!COCKPIT installed (the relay and executor run
inside it), host Python 3.10+, and your automation project (above).

**1. Install** — from a checkout of this repo:

```powershell
py -3 -m venv .venv                    # or any Python >= 3.10
.venv\Scripts\python.exe -m pip install -e .
copy .env.example .env                 # then edit: ECP_PROJECT_ROOT, PLC creds
```

`pip install -e .` pulls in the `mcp` dependency and puts the
`ecockpit-mcp` command on the venv PATH. (Script-only use without
installing: `pip install -r requirements.txt` is enough.)

`.env` keys — see `.env.example` for the full annotated list:
`ECP_PROJECT_ROOT` (required), `ECP_MCP_HOST` (default `127.0.0.1`),
`ECP_MCP_PORT` (default `9777`), `ECP_MCP_TOKEN` (required for non-loopback
binds), plus the project/PLC keys above. The `.env` is found next to the
scripts, then up to two parent levels — a repo-root `.env` works in a
checkout, and `ECP_*` environment variables override it.

**2. Start the server** (host side):

```powershell
# long-lived daemon (local + remote editors), loopback by default
.venv\Scripts\ecockpit-mcp --transport http

# LAN exposure requires a token
.venv\Scripts\ecockpit-mcp --transport http --host 0.0.0.0 --token <secret>
```

CLI flags `--work-dir/--host/--port/--token` override the `.env`.

**3. Connect your editor** (ZCode / any MCP client):

```json
{
  "mcpServers": {
    "ecockpit": {
      "url": "http://127.0.0.1:9777/mcp",
      "headers": { "Authorization": "Bearer <token>" }
    }
  }
}
```

**4. Arm the relay** — with your project open in e!COCKPIT, run once per
session: FILE -> Scripting -> Execute script file ->
`src\ecockpit_mcp\relay.py`. It then polls for commands until stopped
(e!COCKPIT's "Stop script", or create `tmp\ecockpit_server.stop`). Until it
is armed, tools fail with a structured `server_not_armed` error carrying
this same instruction.

**5. Verify** — call the `ecockpit_server_status` tool from your editor, or
run the dev smoke client from the checkout (it talks real MCP: initialize,
tools/list, status, and refusal checks; PLC-touching steps are skipped
unless the relay is armed):

```powershell
.venv\Scripts\python.exe smoke_client.py
```

## Tools

Build & project:

| Tool | Notes |
|---|---|
| `ecockpit_build(skip_import?, all_messages?, harvest_lines?)` | re-import + cleanup + compile; structured diagnostics (`harvest`: C-code, severity, POU, line) |
| `ecockpit_rebuild(all_messages?, harvest_lines?)` | FULL rebuild (`app.rebuild()`) — scripted Build → Clean; fixes C0106 on grown GVL arrays |
| `ecockpit_import` / `ecockpit_compile` | halves of build, standalone |
| `ecockpit_export(dir?)` | PLCopen XML export to `<dir>/<application>.xml` (single file; defaults to `<project root>/plcopensrc-export`) — string-return fallback if the engine refuses to write |
| `ecockpit_snapshot(version?)` | project copy to `<snapshots>/<project>_<version>.ecp` — the working backup (no `save_archive` tool: the engine NREs in every binding, see `docs/SCRIPTENGINE_API.md`) |
| `ecockpit_status` | project/application/device info (no PLC login) |

Online (PLC):

| Tool | Notes |
|---|---|
| `ecockpit_read(variable, device?)` | online variable read (`.env` PLC creds) |
| `ecockpit_write(values, force?, confirm)` | **destructive** — write/force values on the RUNNING PLC; confirm-gated, reads back after write |
| `ecockpit_prepared_values(expression?, device?)` | read-only — staged (prepared) values + currently forced variables |
| `ecockpit_login(option?, delete_foreign_apps?, device?, confirm)` | **destructive** — explicit login with online-change control (`try` = deltas if possible, `force` = deltas or nothing, `never` = full download, `auto` = ladder); session stays open, logout via disconnect |
| `ecockpit_source_download(confirm)` | **destructive** — writes the SOURCE project to the PLC (not the compiled app); saves the project first |
| `ecockpit_download(confirm)` | **destructive** — replaces the RUNNING PLC app; `confirm=true` only on explicit owner request |
| `ecockpit_stop(confirm)` | **destructive** — stops the running PLC application |
| `ecockpit_disconnect(force?)` | PLC logout (`force=true` → forced_disconnect) |

CAUTION — every online command logs in via the online-change ladder
(read-only tools prefer `OnlineChangeOption.Keep`: login without
applying changes or downloading) but deploying commands may still
apply code deltas to the RUNNING application.

System object (the injected `system` global):

| Tool | Notes |
|---|---|
| `ecockpit_system(op?, ...)` | `info` (ui_present, prompt_handling decoded, abort/trace flags, prompt_answers keys) / `write_message` / `set_prompt_handling` (per-command lifetime; `ForwardSimplePrompts` confirm-gated) / `prompt_answers` + `set_prompt_answer` + `clear_prompt_answers` (headless dialog simulation, persists per session) / `set_flags` / `progress` |
| `ecockpit_system_ui(op, ..., confirm)` | **destructive** — `op=ui` pops a REAL modal dialog and blocks until answered (wedge hazard; keep `timeout_s` generous); `op=exit` shuts down e!COCKPIT and the relay (call times out; re-arm needed) |

Project tree + ST authoring:

| Tool | Notes |
|---|---|
| `ecockpit_tree(op?, ...)` | `list` (find/get_children + kind markers, read-only), `rename`/`remove`/`move`/`folder` (**destructive**, confirm-gated) |
| `ecockpit_object_create(kind, name, ..., confirm)` | **destructive** — create pou/gvl/dut/interface/folder/method/property/action/transition; optional `declaration`/`implementation` ST text written on create |
| `ecockpit_textual_read(name, part?)` | read the ST declaration/implementation text |
| `ecockpit_textual_write(name, text, part?, confirm)` | **destructive** — replace the whole declaration or implementation |

Task + symbol configuration:

| Tool | Notes |
|---|---|
| `ecockpit_task_config(application?)` | inspect tasks (kind, priority, interval, watchdog, POUs) — read-only |
| `ecockpit_task_create(application, name, ..., confirm)` | **destructive** — create a task (kind/interval/priority/watchdog/POUs); creates the task configuration when missing |
| `ecockpit_symbol_config_variables(application?, compile?)` | full signature + variable inventory (`compile=true` builds first — slow but safe) |
| `ecockpit_symbol_config_create(application?, ..., confirm)` | **destructive** — add the symbol configuration object |

Device scan / communication / parameters:

| Tool | Notes |
|---|---|
| `ecockpit_device_scan(op?, gateway?, ip?, port?)` | gateway list, network scan (discovered targets), IP→CODESYS-address resolution — read-only |
| `ecockpit_device_comm(op?, ...)` | communication settings read / set IP via gateway (`set_ip` **destructive**, confirm-gated) |
| `ecockpit_device_info(op?, ...)` | device identification (`DeviceId` + module id), simulation mode, driver info (bus cycle task, IO application, stop behaviour, diagnosis); `simulation_set`/`driver_info_set` **destructive**, confirm-gated |
| `ecockpit_device_params(op, ...)` | parameter `list` / `read` (offline or `online=true`) / `write` (**destructive**, confirm-gated, enums validated) |

Application + project lifecycle:

| Tool | Notes |
|---|---|
| `ecockpit_app_status(application?)` | is_active, is_uptodate, is_online_change_possible — read-only |
| `ecockpit_app_control(op, ...)` | `activate` / `boot` (offline boot file on host) / `start` / `reset` warm\|cold\|original — **destructive** except status, confirm-gated |

Project lifecycle + info:

| Tool | Notes |
|---|---|
| `ecockpit_project_control(op, ...)` | `info`/`save` safe; `info_set`/`create`/`open`/`open_archive`/`close` **destructive** (session-mutating; `close` discards unsaved changes) — confirm-gated |
| `ecockpit_plc_fs_ops(op, remote, ...)` | **destructive** — PLC file rename/mkdir/rmdir (recursive) on the ScriptOnlineDevice — confirm-gated |

PLC file system (works via the `ScriptOnlineDevice` created by
`online.create_online_device` — the project-tree device object has no
file API):

| Tool | Notes |
|---|---|
| `ecockpit_plc_ls(directory?)` | list a PLC directory (the runtime root is `.` — `/` is refused; the result reports the probed directory) |
| `ecockpit_plc_get(remote, local)` | download a file from the PLC |
| `ecockpit_plc_put(local, remote, confirm)` | **destructive** — upload a file onto the PLC |
| `ecockpit_plc_rm(remote, confirm)` | **destructive** — delete a PLC file |
| `ecockpit_upload_source(path?)` | read the source project archive back from the PLC |

Libraries (global `librarymanager` / ILibManager — repository curation;
there is no per-application Library Manager scripting API in this build):

| Tool | Notes |
|---|---|
| `ecockpit_libs(repo?, shadowed?, filter?, offset?, limit?)` | list installed libraries (version, company, namespace, dependencies, repo file path); paged — `limit` default 200, `filter` = case-insensitive substring; result carries `total`/`truncated` |
| `ecockpit_lib_find(name, repo?, exact?)` | find by display name (all matches + repository); `exact=true` = first-match-by-name resolution |
| `ecockpit_lib_install(path, repo?, overwrite?, confirm)` | install a compiled-library file into a repository (default: first in search order); `overwrite` needs confirm |
| `ecockpit_lib_uninstall(name, repo?, confirm)` | **destructive** — remove a library from a repository; ambiguous names are refused unless `repo` given |
| `ecockpit_repos()` | repositories in search order: name, root folder, editable, library count |
| `ecockpit_repo_add(rootfolder, name, index?, confirm)` | **destructive** — register a new repository (folder must exist) |
| `ecockpit_repo_remove(name, delete_on_disk?, confirm)` | **destructive** — unregister a repository; `delete_on_disk=true` also deletes the directory |
| `ecockpit_repo_update(name, new_name?, new_location?, copy_libraries?, confirm)` | **destructive** — rename/relocate a repository |
| `ecockpit_repo_move(name, index)` | reorder the search order (first match wins) |

Diagnostics:

| Tool | Notes |
|---|---|
| `ecockpit_server_status` | armed? heartbeat age, last result |
| `ecockpit_messages` / `ecockpit_probe` | scripting-API debugging aids |
| `ecockpit_clear_messages()` | wipe the message store — call before a build for a fresh harvest |

All destructive tools (`download`, `write`, `stop`, `login`,
`source_download`, `lib_uninstall`, `repo_add`, `repo_remove`,
`repo_update`) are confirm-gated at BOTH the MCP layer and the executor
and carry `destructiveHint` annotations.

`build`/`compile`/`rebuild` return `harvest` (structured compiler messages)
and `log_tail` inline on failure, so the model usually needs no second
read.

## ScriptEngine API reference

- `docs/SCRIPTENGINE_API.md` — distilled + live-verified reference for the
  e!COCKPIT ScriptEngine (IScriptApplication.build/rebuild, IScriptMessage
  structured fields, the getattr()-fails-on-interop-objects gotcha, project
  save/save_as semantics, online APIs). Source: the installed
  `ScriptEngine.chm`.
- `docs/SCRIPTENGINE_NOT_EXPOSED.md` — what the CHM documents but these
  tools deliberately do not expose (full-audit inventory: 217 interfaces,
  ~580 methods / ~830 properties), with candidates for future tools.
