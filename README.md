# ecockpit-mcp

MCP (Model Context Protocol) server exposing e!COCKPIT PLC automation to
LLM/AI editors: build, import, compile, online variable read, project
snapshot, live-PLC download — as typed, schema-discoverable tools.

Self-contained: everything that runs on the e!COCKPIT machine ships in this
repo. The only external input is YOUR automation project (pointed at by
`ECP_PROJECT_ROOT`).

## Architecture

```
MCP client (ZCode etc.)
    |  streamable-http  http://<host>:9777/mcp  (bearer token; default)
    |  or stdio         (--transport stdio, client-spawned)
    v
ecockpit_mcp_server.py            <- host Python 3.10+ (this repo)
    |  FILE PROTOCOL (local to the e!COCKPIT machine)
    |  tmp/ecockpit_cmd.json + _cmd_result.json + _watch.heartbeat
    v
ecockpit_cmd_server.py            <- IronPython INSIDE e!COCKPIT (this repo;
    |                               arm once per session: FILE -> Scripting
    |                               -> Execute script file)
    v  exec per command
ecockpit_exec.py                  <- command implementations (this repo;
                                    drives the open project via the
                                    e!COCKPIT ScriptEngine)
```

All three parts read ONE `.env` next to the scripts. The work dir holding
the file-protocol files and logs defaults to `tmp/` next to the scripts
(`ECP_WORK_DIR` overrides).

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

## Setup

```powershell
py -3 -m venv .venv                     # or any Python >= 3.10
.venv\Scripts\python.exe -m pip install -r requirements.txt
copy .env.example .env                  # then edit ECP_PROJECT_ROOT etc.
```

`.env` keys — see `.env.example` for the full annotated list:
`ECP_PROJECT_ROOT` (required), `ECP_MCP_HOST` (default `127.0.0.1`),
`ECP_MCP_PORT` (default `9777`), `ECP_MCP_TOKEN` (required for
non-loopback binds), plus the project/PLC keys above.

## Run

```powershell
# long-lived daemon (local + remote editors), loopback by default
.venv\Scripts\python.exe ecockpit_mcp_server.py --transport http

# LAN exposure requires a token
.venv\Scripts\python.exe ecockpit_mcp_server.py --transport http --host 0.0.0.0 --token <secret>
```

Client config (ZCode / any MCP client):

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

## Tools

Build & project:

| Tool | Notes |
|---|---|
| `ecockpit_build(skip_import?, all_messages?)` | re-import + cleanup + compile; structured diagnostics (`harvest`: C-code, severity, POU, line) |
| `ecockpit_rebuild(all_messages?)` | FULL rebuild (`app.rebuild()`) — scripted Build → Clean; fixes C0106 on grown GVL arrays |
| `ecockpit_import` / `ecockpit_compile` | halves of build, standalone |
| `ecockpit_export(dir?)` | PLCopen XML export (reverse of import; defaults to `<project root>/plcopensrc-export`) |
| `ecockpit_snapshot(version?)` | project copy to `<snapshots>/<project>_<version>.ecp` |
| `ecockpit_save_archive(path?)` | single-file `.projectarchive` backup |
| `ecockpit_status` | project/application/device info (no PLC login) |

Online (PLC):

| Tool | Notes |
|---|---|
| `ecockpit_read(variable, device?)` | online variable read (`.env` PLC creds) |
| `ecockpit_write(values, force?, confirm)` | **destructive** — write/force values on the RUNNING PLC; confirm-gated, reads back after write |
| `ecockpit_unforce_all` | release all forced values |
| `ecockpit_state(device?)` | login/application/operation state readout |
| `ecockpit_download(confirm)` | **destructive** — replaces the RUNNING PLC app; `confirm=true` only on explicit owner request |
| `ecockpit_stop(confirm)` | **destructive** — stops the running PLC application |
| `ecockpit_disconnect(force?)` | PLC logout (`force=true` → forced_disconnect) |

PLC file system:

| Tool | Notes |
|---|---|
| `ecockpit_plc_ls(directory?)` | list a PLC directory |
| `ecockpit_plc_get(remote, local)` | download a file from the PLC |
| `ecockpit_plc_put(local, remote, confirm)` | **destructive** — upload a file onto the PLC |
| `ecockpit_plc_rm(remote, confirm)` | **destructive** — delete a PLC file |
| `ecockpit_upload_source(path?)` | read the source project archive back from the PLC |

Diagnostics:

| Tool | Notes |
|---|---|
| `ecockpit_server_status` | armed? heartbeat age, last result |
| `ecockpit_messages` / `ecockpit_probe` | scripting-API debugging aids |
| `ecockpit_clear_messages()` | wipe the message store — call before a build for a fresh harvest |

All destructive tools (`download`, `write`, `stop`, `plc_put`, `plc_rm`)
are confirm-gated at BOTH the MCP layer and the executor and carry
`destructiveHint` annotations.

`build`/`compile`/`rebuild` return `harvest` (structured compiler messages)
and `log_tail` inline on failure, so the model usually needs no second
read.

## Prerequisite

The e!COCKPIT command server (`ecockpit_cmd_server.py` from this repo) must
be armed once per e!COCKPIT session (FILE -> Scripting -> Execute script
file -> select it). Without it, tools fail with a structured
`server_not_armed` error carrying the re-arm instruction.

## ScriptEngine API reference

- `docs/SCRIPTENGINE_API.md` — distilled + live-verified reference for the
  e!COCKPIT ScriptEngine (IScriptApplication.build/rebuild, IScriptMessage
  structured fields, the getattr()-fails-on-interop-objects gotcha, project
  save/save_as semantics, online APIs). Source: the installed
  `ScriptEngine.chm`.
- `docs/SCRIPTENGINE_NOT_EXPOSED.md` — what the CHM documents but these
  tools deliberately do not expose (full-audit inventory: 217 interfaces,
  ~580 methods / ~830 properties), with candidates for future tools.
