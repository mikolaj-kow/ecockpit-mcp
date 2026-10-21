# -*- coding: utf-8 -*-
"""
ecockpit MCP server - agent-facing MCP layer for e!COCKPIT automation
projects (self-contained: the in-instance scripts ship in this repo).

Architecture (see README):

    MCP client (ZCode etc.)
        |  streamable-http (default, localhost or LAN, bearer token)
        |  or stdio (client-spawned)
        v
    THIS SERVER (host Python 3, project .venv)
        |  FILE PROTOCOL (local to the e!COCKPIT machine):
        |  tmp/ecockpit_cmd.json  (atomic request, id-matched)
        |  tmp/ecockpit_cmd_result.json
        |  tmp/ecockpit_watch.heartbeat
        v
    relay.py (IronPython INSIDE e!COCKPIT, armed via
        |                   FILE -> Scripting -> Execute script file)
        v  exec per command
    executor.py (bundled executor; drives the open project via the
                      e!COCKPIT ScriptEngine)

Transports
----------
    --transport http   (default) streamable-http on --host/--port.
                       Loopback binds may run tokenless; any other host
                       REQUIRES a bearer token (--token / ECP_MCP_TOKEN /
                       .env). Clients configure:
                           url: http://<host>:<port>/mcp
                           headers: {Authorization: Bearer <token>}
    --transport stdio  client-spawned (no network); token not used.

Run as a long-lived daemon for http:
    .venv/Scripts/python.exe src/ecockpit_mcp/server.py --transport http
"""

import argparse
import io
import json
import os
import random
import sys
import threading
import time

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations


# --------------------------------------------------------------------------
# Configuration
# --------------------------------------------------------------------------
def _load_dotenv(path):
    out = {}
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                if key.strip():
                    out[key.strip()] = val.strip()
    except Exception:
        pass
    return out


PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_dotenv(start_dir):
    """Locate the one project .env: next to the scripts, then up to two
    parent levels (the src/ package layout sits two levels below the repo
    root). Anchored at __file__ - never the cwd - so the daemon, the armed
    command relay, and the executor all resolve the SAME file."""
    d = os.path.abspath(start_dir)
    seen = []
    for _ in range(3):
        seen.append(os.path.join(d, ".env"))
        d = os.path.dirname(d)
    for path in seen:
        if os.path.isfile(path):
            return path
    return seen[0]


_DOTENV = _load_dotenv(_find_dotenv(PROJECT_DIR))


def _cfg(name, default=""):
    return _DOTENV.get(name) or os.environ.get(name, "") or default


class Config(object):
    work_dir = _cfg("ECP_WORK_DIR", "")
    host = _cfg("ECP_MCP_HOST", "127.0.0.1")
    port = int(_cfg("ECP_MCP_PORT", "9777"))
    token = _cfg("ECP_MCP_TOKEN", "")
    transport = "http"

    @property
    def tmp_dir(self):
        # File-protocol work dir - shared with relay.py /
        # executor.py (which resolve the same ECP_WORK_DIR/.env key).
        return self.work_dir or os.path.join(PROJECT_DIR, "tmp")

    @property
    def cmd_file(self):
        return os.path.join(self.tmp_dir, "ecockpit_cmd.json")

    @property
    def result_file(self):
        return os.path.join(self.tmp_dir, "ecockpit_cmd_result.json")

    @property
    def heartbeat_file(self):
        return os.path.join(self.tmp_dir, "ecockpit_watch.heartbeat")


CFG = Config()

# Per-command timeouts (seconds). The in-instance engine executes one
# command at a time; an 88-XML import + compile is 15-60 s.
_TIMEOUTS = {
    "build": 900,
    "import": 600,
    "compile": 900,
    "rebuild": 1200,
    "status": 120,
    "read": 180,
    "write": 180,
    "unforce": 120,
    "download": 600,
    "disconnect": 120,
    "stop": 120,
    "state": 120,
    "probe": 300,
    "messages": 300,
    "snapshot": 300,
    "export": 600,
    "plcfs": 300,
    "upload_source": 600,
    "clear_messages": 60,
    "libs": 120,
    "lib_find": 120,
    "lib_install": 120,
    "lib_uninstall": 120,
    "repos": 120,
    "repo_add": 120,
    "repo_remove": 120,
    "repo_update": 120,
    "repo_move": 120,
}
_DEFAULT_TIMEOUT = 300

_SEND_LOCK = threading.Lock()

REARM_HINT = ("e!COCKPIT command relay is not running. Arm it in the open "
              "e!COCKPIT GUI: FILE -> Scripting -> Execute script file -> "
              "relay.py (from this ecockpit-mcp checkout)")


class NotArmedError(RuntimeError):
    pass


# --------------------------------------------------------------------------
# File protocol client (local to the e!COCKPIT machine)
# --------------------------------------------------------------------------
def _heartbeat_age():
    try:
        return time.time() - os.path.getmtime(CFG.heartbeat_file)
    except OSError:
        return 1e9


def _require_heartbeat(max_wait=45.0, fresh=12.0):
    """Fail fast when the in-instance server is dead; tolerate a command
    in flight (the heartbeat stalls while a command runs)."""
    if _heartbeat_age() <= fresh:
        return
    deadline = time.time() + max_wait
    while time.time() < deadline:
        if _heartbeat_age() <= fresh:
            return
        time.sleep(0.5)
    raise NotArmedError(
        "no fresh heartbeat (age {:.0f}s) - {}".format(
            _heartbeat_age(), REARM_HINT))


def _send_command(cmd, args, timeout_s):
    """Send one command and return the id-matched result dict."""
    with _SEND_LOCK:  # the engine executes one command at a time
        _require_heartbeat()
        if not os.path.isdir(CFG.tmp_dir):
            os.makedirs(CFG.tmp_dir)
        rid = "{ts}-{rnd}".format(
            ts=time.strftime("%Y%m%d-%H%M%S"),
            rnd=random.randint(0, 99999))
        request = {"id": rid, "cmd": cmd, "args": args}
        tmp = "{}.{}.tmp".format(CFG.cmd_file, rid)
        with open(tmp, "w", encoding="utf-8", newline="") as fh:
            json.dump(request, fh, separators=(",", ":"))
        try:
            os.remove(CFG.result_file)
        except FileNotFoundError:
            pass
        os.replace(tmp, CFG.cmd_file)  # atomic on the same volume

        deadline = time.time() + timeout_s
        while time.time() < deadline:
            if os.path.isfile(CFG.result_file):
                try:
                    with open(CFG.result_file, "r", encoding="utf-8") as fh:
                        result = json.load(fh)
                    if result.get("id") == rid:
                        return result
                except (ValueError, OSError):
                    pass  # partial/unrelated - keep waiting
            time.sleep(0.25)
        raise TimeoutError(
            "no result for command '{}' (id {}) after {} s - the server may "
            "be busy with a long command".format(cmd, rid, timeout_s))


def _log_tail(path, lines=30):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as fh:
            return fh.read().splitlines()[-lines:]
    except Exception as exc:
        return ["<log tail unavailable: {}>".format(exc)]


def _run(cmd, args=None, timeout_s=None):
    """Send a command and return a JSON-ready dict (tool result)."""
    args = args or {}
    timeout_s = timeout_s or _TIMEOUTS.get(cmd, _DEFAULT_TIMEOUT)
    result = _send_command(cmd, args, timeout_s)
    if not result.get("summary_line"):
        result["summary_line"] = "{c} -> {o}".format(
            c=cmd, o="OK" if result.get("ok") else "FAILED")
    if not result.get("ok"):
        result["log_tail"] = _log_tail(result.get("log", ""), 30)
    return result


# --------------------------------------------------------------------------
# MCP server + tools
# --------------------------------------------------------------------------
mcp = MCPServer(
    name="ecockpit",
    instructions=(
        "Build/import/compile and online operations for a WAGO e!COCKPIT "
        "automation project. All tools drive the e!COCKPIT GUI "
        "instance via its command relay - it must be armed (FILE -> "
        "Scripting -> Execute script file -> relay.py). "
        "Build results include the structured compiler diagnostics "
        "(harvest: C-code, severity, POU, line). Destructive operations "
        "(ecockpit_download, ecockpit_write, ecockpit_stop, "
        "ecockpit_plc_put, ecockpit_plc_rm) touch the RUNNING PLC and are "
        "confirm-gated - only call them on an explicit owner request, with "
        "confirm=true. Library-repository mutations (ecockpit_lib_uninstall, "
        "ecockpit_repo_add/remove/update) change machine-wide library "
        "state and are confirm-gated the same way; ecockpit_libs, "
        "ecockpit_lib_find and ecockpit_repos are read-only."))


@mcp.tool(
    description="Check whether the in-instance e!COCKPIT command relay is "
                "armed (heartbeat freshness) and show the last command result.")
def ecockpit_server_status() -> str:
    age = _heartbeat_age()
    armed = age <= 12
    info = {
        "armed": armed,
        "heartbeat_age_s": round(age, 1) if age < 1e8 else None,
        "rearm_hint": None if armed else REARM_HINT,
    }
    try:
        with open(CFG.result_file, "r", encoding="utf-8") as fh:
            info["last_result"] = json.load(fh)
    except Exception:
        info["last_result"] = None
    return json.dumps(info, indent=2)


@mcp.tool(
    description="Build the PLC project inside the open e!COCKPIT: "
                "re-import all Application.*.xml from the automation "
                "project's plcopensrc/ tree, delete stale POUs "
                "(REMOVED_POUS), compile. Takes ~30-60 s. Returns "
                "structured compiler diagnostics (harvest) with C-code, "
                "severity, POU and line on failure. Codes listed in "
                "IGNORE_CODES (.env, e.g. \"C543, C100\") are filtered from "
                "harvest and counts; set all_messages=true to bypass the "
                "filter for this run. harvest_lines (default 60) raises "
                "how many harvest lines come back inline for big "
                "failures.")
def ecockpit_build(skip_import: bool = False, all_messages: bool = False,
                   harvest_lines: int = 60, timeout_s: int = 900) -> str:
    return json.dumps(_run("build", {"skip_import": bool(skip_import),
                                     "all_messages": bool(all_messages),
                                     "harvest_lines": int(harvest_lines)},
                           timeout_s), indent=2)


@mcp.tool(
    description="Re-import every Application.*.xml into the open e!COCKPIT "
                "project and delete stale POUs - no compile. Takes ~15-30 s.")
def ecockpit_import(timeout_s: int = 600) -> str:
    return json.dumps(_run("import", {}, timeout_s), indent=2)


@mcp.tool(
    description="Export the application objects of the open project back "
                "to PLCopen XML (reverse of import; IExportReporter "
                "captures export errors). dir defaults to "
                "<project root>/plcopensrc-export - point it at your "
                "plcopensrc source tree only when you intentionally want "
                "to update the sources.")
def ecockpit_export(dir: str = "", timeout_s: int = 600) -> str:
    args = {"dir": dir} if dir else {}
    return json.dumps(_run("export", args, timeout_s), indent=2)


@mcp.tool(
    description="Compile only (no XML re-import). Same diagnostics and "
                "IGNORE_CODES filtering as build (all_messages=true "
                "bypasses). harvest_lines (default 60) raises how many "
                "harvest lines come back inline.")
def ecockpit_compile(all_messages: bool = False, harvest_lines: int = 60,
                     timeout_s: int = 900) -> str:
    return json.dumps(_run("compile", {"all_messages": bool(all_messages),
                                       "harvest_lines": int(harvest_lines)},
                           timeout_s), indent=2)


@mcp.tool(
    description="FULL rebuild (app.rebuild() - scripted Build -> Clean): "
                "compile from scratch without XML re-import. Fixes grown "
                "GVL arrays failing incremental builds with C0106 'out of "
                "global data memory'. Slower than compile (~1-3 min). Same "
                "diagnostics and IGNORE_CODES filtering as build. "
                "harvest_lines (default 60) raises how many harvest lines "
                "come back inline.")
def ecockpit_rebuild(all_messages: bool = False, harvest_lines: int = 60,
                     timeout_s: int = 1200) -> str:
    return json.dumps(_run("rebuild", {"all_messages": bool(all_messages),
                                       "harvest_lines": int(harvest_lines)},
                           timeout_s), indent=2)


@mcp.tool(
    description="Project/application/device info of the open e!COCKPIT "
                "project (names, ip, simulated flag). No PLC login.")
def ecockpit_status() -> str:
    return json.dumps(_run("status", {}), indent=2)


@mcp.tool(
    description="Read one variable ONLINE from the live PLC (logs in with "
                ".env PLC credentials, then disconnects). Path is the "
                "application-relative symbol, e.g. GVLName.Variable.")
def ecockpit_read(variable: str, device: str = "") -> str:
    if not variable:
        raise ValueError("variable path is required, e.g. "
                         "'GVLName.Variable'")
    args = {"path": variable}
    if device:
        args["device"] = device
    return json.dumps(_run("read", args), indent=2)


def _iec_literal(value):
    """Format a JSON value as an IEC 61131 literal string."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. Write "
                "values to variables on the RUNNING PLC (logs in, "
                "prepares values, writes, reads back, disconnects). "
                "values maps application-relative paths to IEC literals: "
                "booleans as true/false, numbers as-is, strings passed "
                "through verbatim (quote STRING values yourself, e.g. "
                "\"'hello'\"). force=true forces the values (they stay "
                "forced until ecockpit_unforce_all). Pass confirm=true, "
                "never without an explicit owner instruction.")
def ecockpit_write(values: dict, device: str = "", force: bool = False,
                   confirm: bool = False, timeout_s: int = 180) -> str:
    if not isinstance(values, dict) or not values:
        raise ValueError("values must be a non-empty {variable: value} map")
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "write refused - it changes values on the RUNNING "
                     "PLC application; call again with confirm=true only "
                     "on an explicit owner request",
        }, indent=2)
    prepared = {str(k): _iec_literal(v) for k, v in values.items()}
    args = {"values": prepared, "force": bool(force), "confirm": True}
    if device:
        args["device"] = device
    return json.dumps(_run("write", args, timeout_s), indent=2)


@mcp.tool(
    description="Release ALL forced values on the PLC (unforce_all_values). "
                "Safe direction: restores normal variable writes.")
def ecockpit_unforce_all(device: str = "") -> str:
    args = {"device": device} if device else {}
    return json.dumps(_run("unforce", args), indent=2)


@mcp.tool(
    description="Online state readout of the PLC (logs in briefly, then "
                "disconnects): login state, application_state, "
                "operation_state, device connection. CAUTION: if the PLC "
                "is unreachable this can block the in-instance command "
                "server on a GUI dialog (symptom: heartbeat goes stale, "
                "tools report not-armed until the dialog is dismissed).")
def ecockpit_state(device: str = "") -> str:
    args = {"device": device} if device else {}
    return json.dumps(_run("state", args), indent=2)


@mcp.tool(
    description="Dump the message-store category map (scripting API "
                "debugging aid) and the active IGNORE_CODES filter list.")
def ecockpit_messages() -> str:
    return json.dumps(_run("messages", {}), indent=2)


@mcp.tool(
    description="Clear the e!COCKPIT scripting message store. Call before "
                "a build/compile to guarantee the harvest contains ONLY "
                "that run's diagnostics (no stale messages from earlier "
                "builds or GUI actions).")
def ecockpit_clear_messages() -> str:
    return json.dumps(_run("clear_messages", {}), indent=2)


@mcp.tool(
    description="Save the open project in place and copy it to the "
                "snapshots dir as <project>_<version>.ecp "
                "(ECP_SNAPSHOTS_DIR, default <project root>/snapshots). "
                "Version: explicit arg > sAppVersion in the "
                "ECP_VERSION_POU source XML > dotted number in the "
                "project filename.")
def ecockpit_snapshot(version: str = "") -> str:
    args = {"version": version} if version else {}
    return json.dumps(_run("snapshot", args), indent=2)


@mcp.tool(
    description="Log out of the PLC (releases the login that blocks XML "
                "imports). force=true uses forced_disconnect when a plain "
                "logout hangs.")
def ecockpit_disconnect(force: bool = False) -> str:
    return json.dumps(_run("disconnect", {"force": bool(force)}), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. STOP the "
                "running application on the live PLC and report the "
                "resulting operation state. Pass confirm=true, never "
                "without an explicit owner instruction.")
def ecockpit_stop(confirm: bool = False, device: str = "",
                  timeout_s: int = 120) -> str:
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "stop refused - it STOPS the running PLC "
                     "application; call again with confirm=true only on "
                     "an explicit owner request",
        }, indent=2)
    args = {"confirm": True}
    if device:
        args["device"] = device
    return json.dumps(_run("stop", args, timeout_s), indent=2)


@mcp.tool(
    description="Dump the scripting API surface (dir() + getattr probes) to "
                "tmp/logs/ecockpit_api_probe.txt for API discovery.")
def ecockpit_probe() -> str:
    return json.dumps(_run("probe", {}), indent=2)


@mcp.tool(
    description="List a directory on the PLC file system (logs in, lists, "
                "disconnects). Entries carry name, is_directory, size, "
                "last_modification_time.")
def ecockpit_plc_ls(directory: str = "/", device: str = "",
                    timeout_s: int = 300) -> str:
    args = {"op": "ls", "remote": directory or "/"}
    if device:
        args["device"] = device
    return json.dumps(_run("plcfs", args, timeout_s), indent=2)


@mcp.tool(
    description="Download a file FROM the PLC to this machine (read-only "
                "on the PLC). remote is the PLC path, local the host "
                "destination (overwrites).")
def ecockpit_plc_get(remote: str, local: str, device: str = "",
                     timeout_s: int = 300) -> str:
    if not remote or not local:
        raise ValueError("remote (PLC path) and local (host path) required")
    args = {"op": "get", "remote": remote, "local": local}
    if device:
        args["device"] = device
    return json.dumps(_run("plcfs", args, timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. Upload a "
                "file from this machine ONTO the PLC file system "
                "(overwrites). Pass confirm=true, never without an "
                "explicit owner instruction.")
def ecockpit_plc_put(local: str, remote: str, device: str = "",
                     confirm: bool = False, timeout_s: int = 300) -> str:
    if not local or not remote:
        raise ValueError("local (host path) and remote (PLC path) required")
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "plc_put refused - it writes a file onto the PLC; "
                     "call again with confirm=true only on an explicit "
                     "owner request",
        }, indent=2)
    args = {"op": "put", "local": local, "remote": remote, "confirm": True}
    if device:
        args["device"] = device
    return json.dumps(_run("plcfs", args, timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. DELETE a "
                "file on the PLC file system. Pass confirm=true, never "
                "without an explicit owner instruction.")
def ecockpit_plc_rm(remote: str, device: str = "", confirm: bool = False,
                    timeout_s: int = 300) -> str:
    if not remote:
        raise ValueError("remote (PLC path) required")
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "plc_rm refused - it deletes a file on the PLC; "
                     "call again with confirm=true only on an explicit "
                     "owner request",
        }, indent=2)
    args = {"op": "rm", "remote": remote, "confirm": True}
    if device:
        args["device"] = device
    return json.dumps(_run("plcfs", args, timeout_s), indent=2)


@mcp.tool(
    description="Read the source project archive back from the PLC "
                "(upload_source; useful to recover what is actually "
                "running). path defaults to "
                "<work dir>/plc_source_upload.projectarchive.")
def ecockpit_upload_source(path: str = "", device: str = "",
                           timeout_s: int = 600) -> str:
    args = {"path": path} if path else {}
    if device:
        args["device"] = device
    return json.dumps(_run("upload_source", args, timeout_s), indent=2)


# NOTE: no ecockpit_save_archive tool on purpose - e!COCKPIT 11.3.1.0
# NREs ("Object reference not set") in EVERY documented save_archive
# binding (5 forms probed live 2026-10-21; see docs/SCRIPTENGINE_API.md
# and the executor's cmd_save_archive ladder). ecockpit_snapshot is the
# working backup path. Re-add a tool only once the engine is fixed.


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. Logs into "
                "the live PLC, replaces the RUNNING application with the "
                "open project, and starts it. Pass confirm=true, never "
                "without an explicit owner instruction.")
def ecockpit_download(confirm: bool = False, timeout_s: int = 600) -> str:
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "download refused - it REPLACES the running PLC "
                     "application; call again with confirm=true only on "
                     "an explicit owner request",
        }, indent=2)
    return json.dumps(_run("download", {"confirm": True}, timeout_s),
                      indent=2)


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------
def _run_http():
    host = CFG.host
    token = CFG.token
    loopback = host in ("127.0.0.1", "localhost", "::1")
    if not token and not loopback:
        raise SystemExit(
            "refusing to bind {} without a bearer token - set ECP_MCP_TOKEN "
            "(env or .env) or pass --token".format(host))
    if not token:
        print("[ecockpit-mcp] tokenless loopback bind - set ECP_MCP_TOKEN "
              "to require bearer auth", flush=True)

    app = mcp.streamable_http_app(host=host)

    class BearerTokenMiddleware(object):
        """Pure-ASGI bearer check (skipped when no token is configured)."""

        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            if scope["type"] == "http" and token:
                headers = dict(scope.get("headers") or [])
                supplied = headers.get(b"authorization", b"")
                if supplied != "Bearer {}".format(token).encode():
                    await send({
                        "type": "http.response.start",
                        "status": 401,
                        "headers": [(b"content-type",
                                     b"application/json")],
                    })
                    await send({
                        "type": "http.response.body",
                        "body": b'{"error":"unauthorized - missing or '
                                b'invalid bearer token"}',
                    })
                    return
            await self.inner(scope, receive, send)

    import uvicorn
    print("[ecockpit-mcp] streamable-http on http://{h}:{p}/mcp "
          "(token: {t})".format(
              h=host, p=CFG.port,
              t="required" if token else "DISABLED (loopback)"), flush=True)
    uvicorn.run(BearerTokenMiddleware(app), host=host, port=CFG.port,
                log_level="warning")


# --------------------------------------------------------------------------
# Library curation (global `librarymanager` / ILibManager)
# --------------------------------------------------------------------------

@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=False,
        readOnlyHint=True,
        openWorldHint=True),
    description="List the installed (managed) libraries known to the "
                "e!COCKPIT library manager: display name, version, "
                "company, namespace, dependencies, repository file path. "
                "repo scopes the listing to one repository; shadowed=true "
                "also lists libraries shadowed by higher-priority "
                "repositories (default: excluded). filter is a "
                "case-insensitive substring on displayname/title/name - "
                "USE IT (or page via offset) when exploring: the System "
                "repository alone holds ~4000 libraries and results are "
                "paged (limit default 200, 0 = unlimited). The result "
                "carries total/offset/count/truncated for walking pages.")
def ecockpit_libs(repo: str = "", shadowed: bool = False, filter: str = "",
                  offset: int = 0, limit: int = 200,
                  timeout_s: int = 120) -> str:
    args = {}
    if repo:
        args["repo"] = repo
    if shadowed:
        args["shadowed"] = True
    if filter:
        args["filter"] = filter
    if offset:
        args["offset"] = int(offset)
    if limit != 200:
        args["limit"] = int(limit)
    return json.dumps(_run("libs", args, timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=False,
        readOnlyHint=True,
        openWorldHint=True),
    description="Find a library by display name or title. Searches "
                "repositories in their configured order and returns every "
                "match with its repository, file path and a shadowed flag "
                "(true when an earlier repository in search order has the "
                "same library - the first match wins). exact=true matches "
                "the full display name/title only; the default also "
                "accepts the bare title ('NETWORK' finds 'NETWORK, 1.3.5.3 "
                "(OSCAT)'). repo narrows the search.")
def ecockpit_lib_find(name: str, repo: str = "", exact: bool = False,
                      timeout_s: int = 120) -> str:
    if not name:
        raise ValueError("name (library name or display name) required")
    args = {"name": name}
    if repo:
        args["repo"] = repo
    if exact:
        args["exact"] = True
    return json.dumps(_run("lib_find", args, timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=False,
        readOnlyHint=False,
        openWorldHint=True),
    description="Install a compiled-library file (e.g. .library) into a "
                "library repository. repo names the target repository "
                "(default: the FIRST repository in the search order). "
                "overwrite=true replaces an existing library and requires "
                "confirm=true (as does any replacement - call with "
                "confirm only on an explicit owner request). Modifies the "
                "machine-wide library repositories, not the open project.")
def ecockpit_lib_install(path: str, repo: str = "",
                         overwrite: bool = False, confirm: bool = False,
                         timeout_s: int = 120) -> str:
    if not path:
        raise ValueError("path (compiled-library file) required")
    if overwrite and not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "lib_install refused - overwrite=true replaces an "
                     "existing library; call again with confirm=true only "
                     "on an explicit owner request",
        }, indent=2)
    args = {"path": path, "overwrite": bool(overwrite)}
    if repo:
        args["repo"] = repo
    if overwrite:
        args["confirm"] = True
    return json.dumps(_run("lib_install", args, timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. Uninstall "
                "a library from a library repository (removes the files "
                "from the repository; projects referencing it will stop "
                "resolving). If the library exists in several "
                "repositories the command is refused unless repo names "
                "one. Pass confirm=true, never without an explicit owner "
                "instruction.")
def ecockpit_lib_uninstall(name: str, repo: str = "",
                           confirm: bool = False,
                           timeout_s: int = 120) -> str:
    if not name:
        raise ValueError("name (library name or display name) required")
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "lib_uninstall refused - it removes a library from a "
                     "repository; call again with confirm=true only on an "
                     "explicit owner request",
        }, indent=2)
    args = {"name": name, "confirm": True}
    if repo:
        args["repo"] = repo
    return json.dumps(_run("lib_uninstall", args, timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=False,
        readOnlyHint=True,
        openWorldHint=True),
    description="List the library repositories in their configured search "
                "order (first match wins when resolving libraries): "
                "name, root folder, editable flag (system repositories "
                "are not), and per-repository library count.")
def ecockpit_repos(timeout_s: int = 120) -> str:
    return json.dumps(_run("repos", {}, timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. Register "
                "a NEW library repository (machine-wide change). "
                "rootfolder must already exist on disk; index places it "
                "in the search order (-1 = append at the end). Pass "
                "confirm=true, never without an explicit owner "
                "instruction.")
def ecockpit_repo_add(rootfolder: str, name: str, index: int = -1,
                      confirm: bool = False, timeout_s: int = 120) -> str:
    if not rootfolder or not name:
        raise ValueError("rootfolder (existing directory) and name "
                         "required")
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "repo_add refused - it changes the machine-wide "
                     "library repository list; call again with "
                     "confirm=true only on an explicit owner request",
        }, indent=2)
    return json.dumps(_run("repo_add",
                           {"rootfolder": rootfolder, "name": name,
                            "index": int(index), "confirm": True},
                           timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. Unregister "
                "a library repository. delete_on_disk=true ALSO DELETES "
                "the repository directory with all its library files - "
                "only on an explicit owner instruction. System "
                "repositories are not removable. Pass confirm=true, never "
                "without an explicit owner instruction.")
def ecockpit_repo_remove(name: str, delete_on_disk: bool = False,
                         confirm: bool = False,
                         timeout_s: int = 120) -> str:
    if not name:
        raise ValueError("name (repository name) required")
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "repo_remove refused - it changes the machine-wide "
                     "library repository list (delete_on_disk=true also "
                     "deletes the directory); call again with confirm=true "
                     "only on an explicit owner request",
        }, indent=2)
    return json.dumps(_run("repo_remove",
                           {"name": name,
                            "delete_on_disk": bool(delete_on_disk),
                            "confirm": True},
                           timeout_s), indent=2)


@mcp.tool(
    annotations=ToolAnnotations(
        destructiveHint=True,
        readOnlyHint=False,
        openWorldHint=True),
    description="DESTRUCTIVE - requires explicit owner request. Rename a "
                "library repository (new_name) and/or move it to "
                "new_location (must exist; copy_libraries=true installs "
                "the old location's libraries at the new one when "
                "missing). Pass confirm=true, never without an explicit "
                "owner instruction.")
def ecockpit_repo_update(name: str, new_name: str = "",
                         new_location: str = "",
                         copy_libraries: bool = False,
                         confirm: bool = False,
                         timeout_s: int = 120) -> str:
    if not name:
        raise ValueError("name (repository name) required")
    if not new_name and not new_location:
        raise ValueError("new_name and/or new_location required")
    if not confirm:
        return json.dumps({
            "ok": False,
            "refused": True,
            "error": "repo_update refused - it changes the machine-wide "
                     "library repository list; call again with "
                     "confirm=true only on an explicit owner request",
        }, indent=2)
    return json.dumps(_run("repo_update",
                           {"name": name, "new_name": new_name,
                            "new_location": new_location,
                            "copy_libraries": bool(copy_libraries),
                            "confirm": True},
                           timeout_s), indent=2)


@mcp.tool(
    description="Reorder the library repository search order (first "
                "repository wins when a library name resolves to several "
                "ones). index is the new position, -1 moves the "
                "repository to the end. Reversible settings change - no "
                "confirm needed.")
def ecockpit_repo_move(name: str, index: int, timeout_s: int = 120) -> str:
    if not name:
        raise ValueError("name (repository name) required")
    return json.dumps(_run("repo_move",
                           {"name": name, "index": int(index)},
                           timeout_s), indent=2)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--transport", choices=("http", "stdio"),
                        default=None,
                        help="http (default) or stdio (client-spawned)")
    parser.add_argument("--work-dir", default=None,
                        help="file-protocol work dir (default: tmp/ next "
                             "to this script, or ECP_WORK_DIR)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--token", default=None,
                        help="bearer token for http; required for "
                             "non-loopback binds")
    ns = parser.parse_args()

    if ns.work_dir:
        CFG.work_dir = ns.work_dir
    if ns.host:
        CFG.host = ns.host
    if ns.port:
        CFG.port = ns.port
    if ns.token:
        CFG.token = ns.token
    CFG.transport = ns.transport or "http"

    executor = os.path.join(PROJECT_DIR, "executor.py")
    if not os.path.isfile(executor):
        raise SystemExit(
            "bundled executor missing: {e} - keep the ecockpit-mcp "
            "checkout intact (executor.py next to this "
            "server)".format(e=executor))

    print("[ecockpit-mcp] work dir: {r}".format(r=CFG.tmp_dir),
          flush=True)
    if CFG.transport == "stdio":
        # No print() after this - stdout carries the MCP protocol.
        mcp.run(transport="stdio")
    else:
        _run_http()


if __name__ == "__main__":
    main()
