# Security

How ecockpit-mcp protects the PLC host, what its trust boundaries are,
and where the sharp edges are. Everything here is a statement about the
code in this repo — the enforcement points are linked.

## Transport and bearer token

The default transport is **streamable HTTP on `127.0.0.1:9777`**
(`/mcp` endpoint). Two rules decide whether a token is needed:

- **Any non-loopback bind refuses to start without a token.** `--host
  0.0.0.0` (or any address outside `127.0.0.1`/`localhost`/`::1`)
  without `ECP_MCP_TOKEN` (env or `.env`) or `--token` exits with an
  error at startup — it never serves.
- **A loopback bind MAY run tokenless** and prints
  `token: DISABLED (loopback)` at startup. Tokenless means every local
  process can call the tools; prefer setting a token even on loopback.

The check is a pure-ASGI middleware around the MCP app: it compares the
exact `Authorization: Bearer <token>` header and answers `401` with a
JSON error for anything else. The configured token is never echoed into
logs (the startup banner only says `required` or `DISABLED`).

Clients configure:

```json
{ "url": "http://<host>:9777/mcp",
  "headers": { "Authorization": "Bearer <token>" } }
```

The `stdio` transport is client-spawned, has no listener at all, and
does not use the token.

## Confirm-gated destructive operations

Tools that touch the RUNNING PLC, the project, or machine-wide state
are **confirm-gated at the server boundary**: called without
`confirm=true` they return a refusal as *data*
(`{"ok": false, "refused": true, "error": "… call again with
confirm=true …"}`) — never a crash — and never reach the engine. The
gate is enforced in every tool wrapper *before* any file-protocol
command is written, so a refused call costs nothing and cannot be
"partially applied".

The gated set: `ecockpit_write`, `ecockpit_download`,
`ecockpit_login`, `ecockpit_source_download`, `ecockpit_stop`,
`ecockpit_system_ui` (real modal dialogs and `exit`), project-tree
mutations (`ecockpit_tree` non-list ops), `ecockpit_object_create`,
`ecockpit_textual_write`, `ecockpit_task_create`,
`ecockpit_symbol_config_create` / `_options` (`config_set`),
`ecockpit_device_comm` (`set_ip`), `ecockpit_device_info`
(`simulation_set` / `driver_info_set`), `ecockpit_device_io`
(`set_mapping` / `import_csv`), `ecockpit_device_params` (`write`),
`ecockpit_app_control` (non-status ops), `ecockpit_project_control`
(lifecycle ops), PLC file-system mutations (`plc_put`, `plc_rm`,
`plc_fs_ops` non-ls ops), and library-repository mutations
(`ecockpit_lib_install` overwrite, `ecockpit_lib_uninstall`,
`ecockpit_repo_add` / `_remove` / `_update`).

Two protocol-level invariants are pinned by the conformance test suite
(`tests/test_mcp_conformance.py`): every tool annotated
`destructiveHint` carries a `confirm` parameter in its input schema,
and read-only-annotated tools never *require* `confirm` for their
default call. Clients can therefore render a destructive-op warning
from the annotations alone.

The gate is an agent-discipline guard, **not** an authentication
boundary: anyone who can call the MCP tools can pass `confirm=true`.
Access control is the bearer token (above) plus file-system access to
the work dir (below).

## Online commands can deploy code

This is the sharpest edge. Three tools do not just "read" or "set
values" — they can **change the program running on the PLC**:

- `ecockpit_login` with `option=try`/`force` applies **online changes
  (code deltas) to the RUNNING application**. `option=auto` escalates
  through the ladder on its own. A login also holds the PLC session
  open until `ecockpit_disconnect` releases it (blocking XML imports
  meanwhile).
- `ecockpit_download` **replaces the running application** with the
  open project and starts it.
- `ecockpit_source_download` writes the **source project** to the PLC.

Treat a confirm on these as an owner-level approval of a deployment,
not a formality. If the owner said "upload the fix", say which of the
three paths you intend to take and what happens to the running
application — don't pick a stronger one silently. (Environment note:
on this install a login can fall back to a full download when the
runtime can't find a task gap — see TESTING.md for the live findings.)

Availability caution: `ecockpit_state` / login-family tools against an
unreachable PLC can park the in-instance command relay on a GUI dialog
— every tool then reports "not armed" until the dialog is dismissed
in the GUI. That's a wedge, not a leak.

## Credentials

- PLC credentials (`PLC_USER`/`PLC_PASS` in `.env`, or
  `ECP_PLC_USER`/`ECP_PLC_PASS` in the environment) have **no built-in
  default on purpose** — online commands fail with a fix-it message
  when they are missing instead of trying a wrong default against the
  PLC.
- They are read at call time and passed straight to the engine's login
  calls. They are never formatted into the executor's log lines, never
  included in tool results, and never written to the message store.
- `.env` is gitignored; `.env.example` carries empty keys only.
- The MCP bearer token has the same handling: env/`.env`/CLI only,
  compared in constant use, never logged.

`ecockpit_system_ui` secret dialogs (`query_password`,
`query_credentials`) are the one deliberate exception to "results are
plain JSON": the dialog result passes through the host's result file
(`tmp/ecockpit_cmd_result.json`) **unencrypted** — that file lives on
the e!COCKPIT machine only — and is never logged (pinned by a test that
greps the executor log lines for the secret). Do not point `--work-dir`
at a shared location.

## Trust boundaries summary

| Boundary | Enforced by | Notes |
| --- | --- | --- |
| Network clients → MCP tools | Bearer token (off-loopback binds mandatory) | Pure-ASGI 401 middleware; tokenless loopback possible |
| Agent → destructive ops | `confirm=true` + owner instruction | Refusal is data; annotations advertise the gate |
| MCP host → PLC | File protocol in the work dir | Anyone with filesystem access to `tmp/` can inject commands — keep it local to the e!COCKPIT machine |
| Agent → PLC credentials | No defaults; `.env`/env only | Never logged, never in results |
| Anything → GUI wedges | Headless prompt hardening | The relay re-hardens `LogSimplePrompts` at the start of every command; `system.ui` dialogs are separately confirm-gated |

Nothing in the server makes outbound connections beyond answering its
MCP clients; build/import/read results come from the local e!COCKPIT
instance.
