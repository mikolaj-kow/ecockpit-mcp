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
import socket
import threading
import time

import anyio
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError
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
    # CODESYS V3 runtime port probed by ecockpit_server_status's live
    # reachability check (host-side socket only, never touches the engine).
    plc_probe_port = int(_cfg("ECP_PLC_PROBE_PORT", "") or "2455")

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

    @property
    def plc_state_file(self):
        # Connection sidecar written by executor._note_plc on every real
        # login-ladder attempt (same work dir as the rest of the protocol).
        return os.path.join(self.tmp_dir, "ecockpit_plc_state.json")


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
    "login": 180,
    "source_download": 300,
    "prepared_values": 120,
    "tree": 120,
    "object_create": 300,
    "textual": 120,
    "task_config": 120,
    "symbol_config": 900,
    "device_scan": 300,
    "device_comm": 120,
    "device_info": 120,
    "device_params": 180,
    "device_io": 300,
    "app_control": 300,
    "project_control": 300,
    "clear_messages": 60,
    "system": 300,
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

REARM_HINT = (
    "e!COCKPIT command relay is not running. Arm it in the open "
    "e!COCKPIT GUI: FILE -> Scripting -> Execute script file -> "
    "relay.py (from this ecockpit-mcp checkout)"
)


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


def _plc_last_attempt():
    """Last REAL PLC connection attempt, from the executor's sidecar.

    Written by executor._note_plc around the login ladder only - pre-login
    refusals (confirm gates, G-27) never appear here because they never
    touched the PLC.
    """
    try:
        with open(CFG.plc_state_file, "r", encoding="utf-8") as fh:
            entry = json.load(fh)
        if isinstance(entry, dict) and "online" in entry:
            try:
                entry["age_s"] = round(
                    time.time() - float(entry.get("ts", 0)), 1)
            except (TypeError, ValueError):
                pass
            return entry
    except Exception:
        pass
    return None


def _plc_probe(ip):
    """Live TCP reachability of the PLC's CODESYS runtime port.

    Host-side socket connect ONLY - it never touches e!COCKPIT or the
    relay, so unlike ecockpit_state it cannot wedge on an engine dialog.
    Reachable port proves the device answers on the network; it does not
    prove a login would succeed (see the build's online-change limitation).
    """
    port = CFG.plc_probe_port
    target = "{i}:{p}".format(i=ip, p=port)
    start = time.time()
    try:
        conn = socket.create_connection((ip, port), timeout=1.0)
        conn.close()
    except Exception as exc:
        return {"online": False, "target": target,
                "error": "{c}: {m}".format(
                    c=type(exc).__name__, m=str(exc)[:120])}
    return {"online": True, "target": target,
            "ms": round((time.time() - start) * 1000.0)}


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
        "no fresh heartbeat (age {:.0f}s) - {}".format(_heartbeat_age(), REARM_HINT)
    )


def _send_command(cmd, args, timeout_s):
    """Send one command and return the id-matched result dict."""
    with _SEND_LOCK:  # the engine executes one command at a time
        _require_heartbeat()
        if not os.path.isdir(CFG.tmp_dir):
            os.makedirs(CFG.tmp_dir)
        rid = "{ts}-{rnd}".format(
            ts=time.strftime("%Y%m%d-%H%M%S"), rnd=random.randint(0, 99999)
        )
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
            "be busy with a long command".format(cmd, rid, timeout_s)
        )


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
            c=cmd, o="OK" if result.get("ok") else "FAILED"
        )
    if not result.get("ok"):
        result["log_tail"] = _log_tail(result.get("log", ""), 30)
    return result


async def _report_progress(ctx, elapsed_s, timeout_s, cmd):
    """Best-effort MCP progress notification. A progress-incapable or
    disconnecting client must never fail the underlying command."""
    try:
        await ctx.report_progress(
            round(float(elapsed_s), 1),
            float(timeout_s),
            "e!COCKPIT engine: '{c}' running".format(c=cmd),
        )
    except Exception:
        pass


async def _send_command_async(
    cmd, args, timeout_s, ctx=None, poll_s=0.25, progress_every_s=2.0
):
    """Async twin of _send_command: same id-matched file protocol, but
    the wait runs on the event loop (no worker thread parked for the
    whole build) and MCP progress notifications go out while the engine
    works (progress = elapsed seconds against the timeout). ctx=None
    skips progress (direct calls, tests)."""
    while not _SEND_LOCK.acquire(blocking=False):  # one command at a time
        await anyio.sleep(poll_s)
    try:
        _require_heartbeat()
        if not os.path.isdir(CFG.tmp_dir):
            os.makedirs(CFG.tmp_dir)
        rid = "{ts}-{rnd}".format(
            ts=time.strftime("%Y%m%d-%H%M%S"), rnd=random.randint(0, 99999)
        )
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
        last_report = time.time()
        while True:
            if os.path.isfile(CFG.result_file):
                try:
                    with open(CFG.result_file, "r", encoding="utf-8") as fh:
                        result = json.load(fh)
                    if result.get("id") == rid:
                        if ctx is not None:
                            await _report_progress(
                                ctx,
                                time.time() - (deadline - timeout_s),
                                timeout_s,
                                cmd,
                            )
                        return result
                except (ValueError, OSError):
                    pass  # partial/unrelated - keep waiting
            remaining = deadline - time.time()
            if remaining <= 0:
                raise TimeoutError(
                    "no result for command '{}' (id {}) after {} s - the server "
                    "may be busy with a long command".format(cmd, rid, timeout_s)
                )
            now = time.time()
            if ctx is not None and now - last_report >= progress_every_s:
                await _report_progress(ctx, timeout_s - remaining, timeout_s, cmd)
                last_report = now
            await anyio.sleep(poll_s)
    finally:
        _SEND_LOCK.release()


async def _run_async(cmd, args=None, timeout_s=None, ctx=None, **poll_kwargs):
    """Async twin of _run for the long-running tools (default timeout
    >= 300 s): emits MCP progress notifications while waiting (see
    _send_command_async) instead of leaving the client blind."""
    args = args or {}
    timeout_s = timeout_s or _TIMEOUTS.get(cmd, _DEFAULT_TIMEOUT)
    result = await _send_command_async(cmd, args, timeout_s, ctx, **poll_kwargs)
    if not result.get("summary_line"):
        result["summary_line"] = "{c} -> {o}".format(
            c=cmd, o="OK" if result.get("ok") else "FAILED"
        )
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
        "ecockpit_login, ecockpit_source_download) touch the RUNNING PLC "
        "and are confirm-gated - only call them on an explicit owner "
        "request, with confirm=true. Library-repository mutations "
        "(ecockpit_lib_uninstall, ecockpit_repo_add/remove/update) change "
        "machine-wide library state and are confirm-gated the same way; "
        "ecockpit_libs, "
        "ecockpit_lib_find and ecockpit_repos are read-only. "
        "Long-running tools (build/import/compile/rebuild, export, "
        "download/login family, symbol config, device scan, project "
        "lifecycle) emit MCP progress notifications (elapsed vs timeout) "
        "while the engine works instead of leaving the call silent."
    ),
)


@mcp.tool(
    structured_output=False,
    description="Check whether the in-instance e!COCKPIT command relay is "
    "armed (heartbeat freshness), show the last command result, and "
    "report PLC connection status: 'plc.last_attempt' (executor's last "
    "login-ladder outcome) and 'plc.probe' (live host-side TCP probe of "
    "the CODESYS runtime port - cannot wedge the engine)."
)
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
    last = _plc_last_attempt()
    plc = {"last_attempt": last, "probe": None}
    if last and last.get("ip"):
        plc["probe"] = _plc_probe(str(last["ip"]))
    info["plc"] = plc
    return json.dumps(info, indent=2)


@mcp.tool(
    structured_output=False,
    description="Build the PLC project inside the open e!COCKPIT: "
    "re-import all Application.*.xml from the automation "
    "project's plcopensrc/ tree, delete stale POUs "
    "(REMOVED_POUS), compile. Takes ~30-60 s. Returns "
    "structured compiler diagnostics (harvest) with C-code, "
    "severity, POU and line on failure. Codes listed in "
    'IGNORE_CODES (.env, e.g. "C543, C100") are filtered from '
    "harvest and counts; set all_messages=true to bypass the "
    "filter for this run. harvest_lines (default 60) raises "
    "how many harvest lines come back inline for big "
    "failures."
)
async def ecockpit_build(
    skip_import: bool = False,
    all_messages: bool = False,
    harvest_lines: int = 60,
    timeout_s: int = 900,
    ctx: Context | None = None,
) -> str:
    return json.dumps(
        await _run_async(
            "build",
            {
                "skip_import": bool(skip_import),
                "all_messages": bool(all_messages),
                "harvest_lines": int(harvest_lines),
            },
            timeout_s,
            ctx,
        ),
        indent=2,
    )


@mcp.tool(
    structured_output=False,
    description="Re-import every Application.*.xml into the open e!COCKPIT "
    "project and delete stale POUs - no compile. Takes ~15-30 s."
)
async def ecockpit_import(timeout_s: int = 600, ctx: Context | None = None) -> str:
    return json.dumps(await _run_async("import", {}, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    description="Export the application objects of the open project back "
    "to PLCopen XML (reverse of import; IExportReporter "
    "captures export errors). dir defaults to "
    "<project root>/plcopensrc-export - point it at your "
    "plcopensrc source tree only when you intentionally want "
    "to update the sources."
)
async def ecockpit_export(
    dir: str = "", timeout_s: int = 600, ctx: Context | None = None
) -> str:
    args: dict[str, object] = {"dir": dir} if dir else {}
    return json.dumps(await _run_async("export", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    description="Compile only (no XML re-import). Same diagnostics and "
    "IGNORE_CODES filtering as build (all_messages=true "
    "bypasses). harvest_lines (default 60) raises how many "
    "harvest lines come back inline."
)
async def ecockpit_compile(
    all_messages: bool = False,
    harvest_lines: int = 60,
    timeout_s: int = 900,
    ctx: Context | None = None,
) -> str:
    return json.dumps(
        await _run_async(
            "compile",
            {"all_messages": bool(all_messages), "harvest_lines": int(harvest_lines)},
            timeout_s,
            ctx,
        ),
        indent=2,
    )


@mcp.tool(
    structured_output=False,
    description="FULL rebuild (app.rebuild() - scripted Build -> Clean): "
    "compile from scratch without XML re-import. Fixes grown "
    "GVL arrays failing incremental builds with C0106 'out of "
    "global data memory'. Slower than compile (~1-3 min). Same "
    "diagnostics and IGNORE_CODES filtering as build. "
    "harvest_lines (default 60) raises how many harvest lines "
    "come back inline."
)
async def ecockpit_rebuild(
    all_messages: bool = False,
    harvest_lines: int = 60,
    timeout_s: int = 1200,
    ctx: Context | None = None,
) -> str:
    return json.dumps(
        await _run_async(
            "rebuild",
            {"all_messages": bool(all_messages), "harvest_lines": int(harvest_lines)},
            timeout_s,
            ctx,
        ),
        indent=2,
    )


@mcp.tool(
    structured_output=False,
    description="Project/application/device info of the open e!COCKPIT "
    "project (names, ip, simulated flag). No PLC login."
)
def ecockpit_status() -> str:
    return json.dumps(_run("status", {}), indent=2)


@mcp.tool(
    structured_output=False,
    description="Read one variable ONLINE from the live PLC (logs in with "
    ".env PLC credentials, then disconnects). Path is the "
    "application-relative symbol, e.g. GVLName.Variable."
)
def ecockpit_read(variable: str, device: str = "") -> str:
    if not variable:
        raise ToolError("variable path is required, e.g. 'GVLName.Variable'")
    args: dict[str, object] = {"path": variable}
    if device:
        args["device"] = device
    return json.dumps(_run("read", args), indent=2)


def _iec_literal(value):
    """Format a JSON value as an IEC 61131 literal string."""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    return str(value)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Write "
    "values to variables on the RUNNING PLC (logs in, "
    "prepares values, writes, reads back, disconnects). "
    "values maps application-relative paths to IEC literals: "
    "booleans as true/false, numbers as-is, strings passed "
    "through verbatim (quote STRING values yourself, e.g. "
    "\"'hello'\"). force=true forces the values (they stay "
    "forced until ecockpit_unforce_all). Pass confirm=true, "
    "never without an explicit owner instruction.",
)
def ecockpit_write(
    values: dict,
    device: str = "",
    force: bool = False,
    confirm: bool = False,
    timeout_s: int = 180,
) -> str:
    if not isinstance(values, dict) or not values:
        raise ToolError("values must be a non-empty {variable: value} map")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "write refused - it changes values on the RUNNING "
                "PLC application; call again with confirm=true only "
                "on an explicit owner request",
            },
            indent=2,
        )
    prepared = {str(k): _iec_literal(v) for k, v in values.items()}
    args: dict[str, object] = {
        "values": prepared,
        "force": bool(force),
        "confirm": True,
    }
    if device:
        args["device"] = device
    return json.dumps(_run("write", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    description="Release ALL forced values on the PLC (unforce_all_values). "
    "Safe direction: restores normal variable writes."
)
def ecockpit_unforce_all(device: str = "") -> str:
    args: dict[str, object] = {"device": device} if device else {}
    return json.dumps(_run("unforce", args), indent=2)


@mcp.tool(
    structured_output=False,
    description="Online state readout of the PLC (logs in briefly, then "
    "disconnects): login state, application_state, "
    "operation_state, device connection. CAUTION: if the PLC "
    "is unreachable this can block the in-instance command "
    "server on a GUI dialog (symptom: heartbeat goes stale, "
    "tools report not-armed until the dialog is dismissed)."
)
def ecockpit_state(device: str = "") -> str:
    args: dict[str, object] = {"device": device} if device else {}
    return json.dumps(_run("state", args), indent=2)


@mcp.tool(
    structured_output=False,
    description="Dump the message-store category map (scripting API "
    "debugging aid) and the active IGNORE_CODES filter list."
)
def ecockpit_messages() -> str:
    return json.dumps(_run("messages", {}), indent=2)


@mcp.tool(
    structured_output=False,
    description="Clear the e!COCKPIT scripting message store. Call before "
    "a build/compile to guarantee the harvest contains ONLY "
    "that run's diagnostics (no stale messages from earlier "
    "builds or GUI actions)."
)
def ecockpit_clear_messages() -> str:
    return json.dumps(_run("clear_messages", {}), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=False, open_world_hint=True
    ),
    description="Access the e!COCKPIT `system` scripting object. op=info "
    "(read-only: ui_present, prompt_handling decoded, "
    "abort/trace flags, prompt_answers keys, commands). "
    "op=write_message {severity: FatalError|Error|Warning|"
    "Information|Text, text}. op=set_prompt_handling {value: "
    "flag names '+'-combined, e.g. LogSimplePrompts+"
    "LogMessageKeys; confirm=true only needed when it "
    "enables ForwardSimplePrompts (GUI dialogs back on)} - "
    "NOTE the relay re-hardens LogSimplePrompts at the "
    "START of every command, so a set lasts only for the "
    "current command. op=prompt_answers (list) / "
    "op=set_prompt_answer {key, result: OK|Cancel|Yes|No|"
    "Retry|Abort|Ignore} / op=clear_prompt_answers - "
    "headless dialog simulation for engine prompts; these "
    "PERSIST for the e!COCKPIT session. op=set_flags "
    "{trace?, abortable?, abort_autocheck?}. op=progress "
    "{action: start|step|absolute, description, items, "
    "unit, item, completed, absolute_progress} - progress "
    "bar chrome. Modal system.ui dialogs and exit live in "
    "ecockpit_system_ui (separately confirm-gated).",
)
def ecockpit_system(
    op: str = "info",
    value: str = "",
    key: str = "",
    result: str = "",
    text: str = "",
    severity: str = "Information",
    trace: bool | None = None,
    abortable: bool | None = None,
    abort_autocheck: bool | None = None,
    action: str = "",
    description: str = "",
    items: int = -1,
    unit: str = "",
    item: str = "",
    completed: int = 1,
    absolute_progress: int = 0,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    valid = (
        "info",
        "write_message",
        "set_prompt_handling",
        "prompt_answers",
        "set_prompt_answer",
        "clear_prompt_answers",
        "set_flags",
        "progress",
    )
    if op not in valid:
        raise ToolError("op must be one of " + "|".join(valid))
    args: dict[str, object] = {"op": op}
    if op == "write_message":
        if not text:
            raise ToolError("text required for op=write_message")
        args["text"] = text
        args["severity"] = severity
    if op == "set_prompt_handling":
        if not value:
            raise ToolError("value required for op=set_prompt_handling")
        names = [t for t in value.replace(",", "+").replace(" ", "+").split("+") if t]
        if "ForwardSimplePrompts" in names and not confirm:
            return json.dumps(
                {
                    "ok": False,
                    "refused": True,
                    "error": "set_prompt_handling refused - "
                    "ForwardSimplePrompts re-enables GUI dialogs "
                    "(wedge hazard); call again with confirm=true "
                    "only on an explicit owner request",
                },
                indent=2,
            )
        args["value"] = value
        args["confirm"] = True
    if op == "set_prompt_answer":
        if not key or not result:
            raise ToolError("key and result required for op=set_prompt_answer")
        args["key"] = key
        args["result"] = result
    if op == "set_flags":
        if trace is None and abortable is None and abort_autocheck is None:
            raise ToolError(
                "at least one of trace, abortable, abort_autocheck required"
            )
        if trace is not None:
            args["trace"] = bool(trace)
        if abortable is not None:
            args["abortable"] = bool(abortable)
        if abort_autocheck is not None:
            args["abort_autocheck"] = bool(abort_autocheck)
    if op == "progress":
        if not action:
            raise ToolError("action required for op=progress")
        args["action"] = action
        if description:
            args["description"] = description
        if items != -1:
            args["items"] = int(items)
        if unit:
            args["unit"] = unit
        if item:
            args["item"] = item
        if action == "step":
            args["completed"] = int(completed)
        if action == "absolute":
            args["absolute_progress"] = int(absolute_progress)
    return json.dumps(_run("system", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request + "
    "confirm=true. op=ui calls a REAL modal system.ui "
    "dialog that blocks until the owner answers it in the "
    "e!COCKPIT GUI (the relay stalls meanwhile and the "
    "heartbeat goes stale - that is normal, keep "
    "timeout_s generous). method: error|warning|info "
    "(message) | prompt (message, choice, default_result) "
    "| select_many (message, choice, items) | choose "
    "(message, options, initial_selection, cancellable) | "
    "reset_stored_result (store_key) | open_file_dialog / "
    "save_file_dialog (title, filename, directory, filter, "
    "filter_index, default_extension, multiselect) | "
    "browse_directory_dialog (message, path, "
    "new_folder_button) | query_string (message, text, "
    "multi_line) | query_password | query_credentials "
    "(secret values pass through the result file "
    "unencrypted on this host and are never logged). "
    "Prefer prompt_answers simulation (ecockpit_system) "
    "for headless runs - ui.* dialogs ignore it. op=exit "
    "{code} SHUTS DOWN e!COCKPIT and the armed relay: "
    "every tool goes dark until you re-arm a fresh GUI "
    "session; expect the call to time out (no result file "
    "is written).",
)
async def ecockpit_system_ui(
    op: str,
    method: str = "",
    message: str = "",
    message_key: str = "",
    choice: str = "YesNo",
    default_result: str = "",
    items: list | None = None,
    options: list | None = None,
    initial_selection: int = 0,
    cancellable: bool = True,
    store_key: str = "",
    title: str = "",
    filename: str = "",
    directory: str = "",
    filter: str = "",
    filter_index: int | None = None,
    default_extension: str = "",
    multiselect: bool = False,
    path: str = "",
    new_folder_button: bool = True,
    text: str = "",
    multi_line: bool = False,
    username: str = "",
    password: str = "",
    code: int = 0,
    confirm: bool = False,
    timeout_s: int = 600,
    ctx: Context | None = None,
) -> str:
    if op not in ("ui", "exit"):
        raise ToolError("op must be ui|exit")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "system {o} refused - {r}; call again with "
                "confirm=true only on an explicit owner "
                "request".format(
                    o=op,
                    r=(
                        "ui dialogs block the relay on a real GUI dialog (wedge hazard)"
                        if op == "ui"
                        else "exit shuts down the whole e!COCKPIT process"
                    ),
                ),
            },
            indent=2,
        )
    args: dict[str, object] = {"op": op, "confirm": True}
    if op == "exit":
        args["code"] = int(code)
        return json.dumps(
            await _run_async("system", args, min(int(timeout_s), 30), ctx), indent=2
        )
    if not method:
        raise ToolError("method required for op=ui")
    args["method"] = method
    for k, v in (
        ("message", message),
        ("message_key", message_key),
        ("choice", choice),
        ("default_result", default_result),
        ("store_key", store_key),
        ("title", title),
        ("filename", filename),
        ("directory", directory),
        ("filter", filter),
        ("default_extension", default_extension),
        ("path", path),
        ("text", text),
        ("username", username),
        ("password", password),
    ):
        if v:
            args[k] = v
    list_pairs: list[tuple[str, list[str] | None]] = [
        ("items", items),
        ("options", options),
    ]
    for k, lst in list_pairs:
        if lst:
            args[k] = [str(x) for x in lst]
    for k, flag in (
        ("initial_selection", initial_selection),
        ("cancellable", cancellable),
        ("multiselect", multiselect),
        ("new_folder_button", new_folder_button),
        ("multi_line", multi_line),
    ):
        args[k] = flag
    if filter_index is not None:
        args["filter_index"] = int(filter_index)
    return json.dumps(await _run_async("system", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    description="Save the open project in place and copy it to the "
    "snapshots dir as <project>_<version>.ecp "
    "(ECP_SNAPSHOTS_DIR, default <project root>/snapshots). "
    "Version: explicit arg > sAppVersion in the "
    "ECP_VERSION_POU source XML > dotted number in the "
    "project filename."
)
async def ecockpit_snapshot(version: str = "", ctx: Context | None = None) -> str:
    args: dict[str, object] = {"version": version} if version else {}
    return json.dumps(await _run_async("snapshot", args, ctx=ctx), indent=2)


@mcp.tool(
    structured_output=False,
    description="Log out of the PLC (releases the login that blocks XML "
    "imports). force=true uses forced_disconnect when a plain "
    "logout hangs."
)
def ecockpit_disconnect(force: bool = False) -> str:
    return json.dumps(_run("disconnect", {"force": bool(force)}), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. STOP the "
    "running application on the live PLC and report the "
    "resulting operation state. Pass confirm=true, never "
    "without an explicit owner instruction.",
)
def ecockpit_stop(confirm: bool = False, device: str = "", timeout_s: int = 120) -> str:
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "stop refused - it STOPS the running PLC "
                "application; call again with confirm=true only on "
                "an explicit owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {"confirm": True}
    if device:
        args["device"] = device
    return json.dumps(_run("stop", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    description="List a directory on the PLC file system (logs in, lists, "
    "disconnects). Entries carry name, is_directory, size, "
    "last_modification_time. Works via the ScriptOnlineDevice "
    "created by online.create_online_device - the "
    "project-tree device object has no file API."
)
async def ecockpit_plc_ls(
    directory: str = "/",
    device: str = "",
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    args: dict[str, object] = {"op": "ls", "remote": directory or "/"}
    if device:
        args["device"] = device
    return json.dumps(await _run_async("plcfs", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    description="Download a file FROM the PLC to this machine (read-only "
    "on the PLC). remote is the PLC path, local the host "
    "destination (overwrites)."
)
async def ecockpit_plc_get(
    remote: str,
    local: str,
    device: str = "",
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if not remote or not local:
        raise ToolError("remote (PLC path) and local (host path) required")
    args: dict[str, object] = {"op": "get", "remote": remote, "local": local}
    if device:
        args["device"] = device
    return json.dumps(await _run_async("plcfs", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Upload a "
    "file from this machine ONTO the PLC file system "
    "(overwrites). Pass confirm=true, never without an "
    "explicit owner instruction.",
)
async def ecockpit_plc_put(
    local: str,
    remote: str,
    device: str = "",
    force: bool = True,
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if not local or not remote:
        raise ToolError("local (host path) and remote (PLC path) required")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "plc_put refused - it writes a file onto the PLC; "
                "call again with confirm=true only on an explicit "
                "owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {
        "op": "put",
        "local": local,
        "remote": remote,
        "force": bool(force),
        "confirm": True,
    }
    if device:
        args["device"] = device
    return json.dumps(await _run_async("plcfs", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. DELETE a "
    "file on the PLC file system. Pass confirm=true, never "
    "without an explicit owner instruction.",
)
async def ecockpit_plc_rm(
    remote: str,
    device: str = "",
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if not remote:
        raise ToolError("remote (PLC path) required")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "plc_rm refused - it deletes a file on the PLC; "
                "call again with confirm=true only on an explicit "
                "owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {"op": "rm", "remote": remote, "confirm": True}
    if device:
        args["device"] = device
    return json.dumps(await _run_async("plcfs", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    description="Read the source project archive back from the PLC "
    "(upload_source on the ScriptOnlineDevice; useful to "
    "recover what is actually running). path defaults to "
    "<work dir>/plc_source_upload.projectarchive."
)
async def ecockpit_upload_source(
    path: str = "",
    device: str = "",
    timeout_s: int = 600,
    ctx: Context | None = None,
) -> str:
    args: dict[str, object] = {"path": path} if path else {}
    if device:
        args["device"] = device
    return json.dumps(await _run_async("upload_source", args, timeout_s, ctx), indent=2)


# NOTE: no ecockpit_save_archive tool on purpose - e!COCKPIT 11.3.1.0
# NREs ("Object reference not set") in EVERY documented save_archive
# binding (6 forms probed live 2026-10-21/22, INCLUDING with a saved,
# named project; see docs/SCRIPTENGINE_API.md and the executor's
# cmd_save_archive ladder). ecockpit_snapshot is the working backup
# path. Re-add a tool only once the engine is fixed.


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Logs into "
    "the live PLC, replaces the RUNNING application with the "
    "open project, and starts it. Pass confirm=true, never "
    "without an explicit owner instruction.",
)
async def ecockpit_download(
    confirm: bool = False, timeout_s: int = 600, ctx: Context | None = None
) -> str:
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "download refused - it REPLACES the running PLC "
                "application; call again with confirm=true only on "
                "an explicit owner request",
            },
            indent=2,
        )
    return json.dumps(
        await _run_async("download", {"confirm": True}, timeout_s, ctx), indent=2
    )


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Explicit "
    "login with online-change control; the session STAYS "
    "open (logout with ecockpit_disconnect). option: 'try' = "
    "apply online changes if possible, else full download; "
    "'force' = online changes or nothing; 'never' = full "
    "download forced; 'auto' = ladder (Try -> Force -> Never "
    "-> connect). CAUTION: a login can apply online changes "
    "(code deltas) to the RUNNING application. Pass "
    "confirm=true, never without an explicit owner "
    "instruction.",
)
def ecockpit_login(
    option: str = "try",
    delete_foreign_apps: bool = False,
    device: str = "",
    confirm: bool = False,
    timeout_s: int = 180,
) -> str:
    if option not in ("try", "force", "never", "auto"):
        raise ToolError("option must be try|force|never|auto")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "login refused - it can apply online changes (code "
                "deltas) to the RUNNING PLC application; call again "
                "with confirm=true only on an explicit owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {
        "option": option,
        "delete_foreign_apps": bool(delete_foreign_apps),
        "confirm": True,
    }
    if device:
        args["device"] = device
    return json.dumps(_run("login", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Write the "
    "SOURCE project to the PLC (source_download) - unlike "
    "ecockpit_download this ships the sources, not the "
    "compiled application. Saves the open project first (it "
    "NREs on unsaved changes otherwise). Pass confirm=true, "
    "never without an explicit owner instruction.",
)
async def ecockpit_source_download(
    confirm: bool = False, timeout_s: int = 300, ctx: Context | None = None
) -> str:
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "source_download refused - it writes the source "
                "project to the PLC; call again with confirm=true "
                "only on an explicit owner request",
            },
            indent=2,
        )
    return json.dumps(
        await _run_async("source_download", {"confirm": True}, timeout_s, ctx), indent=2
    )


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="Read back staged (prepared) and forced values from the "
    "online application: every staged expression with its "
    "prepared value (get_prepared_expressions + "
    "get_prepared_value), or just `expression`'s staged "
    "value, plus the list of currently forced variables "
    "(get_forced_expressions). Read-only.",
)
def ecockpit_prepared_values(
    expression: str = "", device: str = "", timeout_s: int = 120
) -> str:
    args: dict[str, object] = {}
    if expression:
        args["expression"] = expression
    if device:
        args["device"] = device
    return json.dumps(_run("prepared_values", args, timeout_s), indent=2)


# --------------------------------------------------------------------------
# Project tree + ST authoring (ScriptLib stubs)
# --------------------------------------------------------------------------


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="List/search the project tree: object names, GUIDs and "
    "kind markers (device/application/folder/symbol config/"
    "textual flags). name filters via project.find; "
    "recursive defaults true; limit default 200.",
)
def ecockpit_tree(
    op: str = "list",
    name: str = "",
    new_name: str = "",
    new_parent: str = "",
    recursive: bool = True,
    limit: int = 200,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if op not in ("list", "rename", "remove", "move", "folder"):
        raise ToolError("op must be list|rename|remove|move|folder")
    if op != "list" and not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "tree {o} refused - it modifies the project; call "
                "again with confirm=true only on an explicit owner "
                "request".format(o=op),
            },
            indent=2,
        )
    args: dict[str, object] = {"op": op}
    if name:
        args["name"] = name
    if new_name:
        args["new_name"] = new_name
    if new_parent:
        args["new_parent"] = new_parent
    if op == "list":
        args["recursive"] = bool(recursive)
        args["limit"] = int(limit)
    if op != "list":
        args["confirm"] = True
    return json.dumps(_run("tree", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Create a "
    "project object: kind pou (pou_type program|"
    "functionblock|function, ST), gvl, dut, interface, "
    "folder, special_pou (implicit-check POU via "
    "create_pou(SpecialPouType): special_type check_bounds|"
    "check_pointer|check_range_unsigned|check_range_signed|"
    "check_div_real_64|check_div_real_32|check_div_int_64|"
    "check_div_int_32|check_l_range_unsigned|"
    "check_l_range_signed - the engine names the object "
    "itself; WARNING: adding a check function FORCES A FULL "
    "RECOMPILE and prohibits online change from then on), "
    "or a POU member (method/property/action/transition; "
    "needs parent). Optional declaration/implementation ST "
    "text is written into the new object. Modifies the "
    "project; the new code is compiled on the next build. "
    "Pass confirm=true, never without an explicit owner "
    "instruction.",
)
async def ecockpit_object_create(
    kind: str,
    name: str = "",
    parent: str = "",
    pou_type: str = "functionblock",
    special_type: str = "",
    return_type: str = "",
    base_type: str = "",
    declaration: str = "",
    implementation: str = "",
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "object_create refused - it modifies the project "
                "(new code objects); call again with confirm=true "
                "only on an explicit owner request",
            },
            indent=2,
        )
    if kind == "special_pou":
        if not special_type:
            raise ToolError("special_type required for kind=special_pou")
        args: dict[str, object] = {
            "kind": kind,
            "special_type": special_type,
            "confirm": True,
        }
        if parent:
            args["parent"] = parent
        return json.dumps(
            await _run_async("object_create", args, timeout_s, ctx), indent=2
        )
    if not kind or not name:
        raise ToolError("kind and name required")
    args = {"kind": kind, "name": name, "confirm": True}
    if parent:
        args["parent"] = parent
    if pou_type:
        args["pou_type"] = pou_type
    if return_type:
        args["return_type"] = return_type
    if base_type:
        args["base_type"] = base_type
    if declaration:
        args["declaration"] = declaration
    if implementation:
        args["implementation"] = implementation
    return json.dumps(await _run_async("object_create", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="Read the ST text of a textual object: declaration and/"
    "or implementation (ScriptTextDocument). part selects "
    "one; default returns both. Read-only.",
)
def ecockpit_textual_read(name: str, part: str = "", timeout_s: int = 120) -> str:
    if not name:
        raise ToolError("name (object name) required")
    args: dict[str, object] = {"op": "read", "name": name}
    if part:
        args["part"] = part
    return json.dumps(_run("textual", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Replace "
    "the whole declaration or implementation ST text of an "
    "object (ScriptTextDocument.replace). This rewrites "
    "project code - it is compiled on the next build. Pass "
    "confirm=true, never without an explicit owner "
    "instruction.",
)
def ecockpit_textual_write(
    name: str,
    text: str,
    part: str = "implementation",
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if not name or text is None:
        raise ToolError("name and text required")
    if part not in ("declaration", "implementation"):
        raise ToolError("part must be declaration|implementation")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "textual_write refused - it REPLACES project code; "
                "call again with confirm=true only on an explicit "
                "owner request",
            },
            indent=2,
        )
    return json.dumps(
        _run(
            "textual",
            {"op": "write", "name": name, "part": part, "text": text, "confirm": True},
            timeout_s,
        ),
        indent=2,
    )


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="Inspect the application's task configuration: tasks "
    "with kind, priority, interval, watchdog and assigned "
    "POUs. Read-only.",
)
def ecockpit_task_config(application: str = "", timeout_s: int = 120) -> str:
    args: dict[str, object] = {}
    if application:
        args["application"] = application
    return json.dumps(_run("task_config", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Create a "
    "task in the application's task configuration (creates "
    "the configuration object when missing): kind cyclic|"
    "freewheeling|event|external_event|status|"
    "parent_synchron, interval + unit (cyclic), priority, "
    "watchdog, pous list. Modifies the project; compiled on "
    "the next build. Pass confirm=true, never without an "
    "explicit owner instruction.",
)
def ecockpit_task_create(
    application: str,
    name: str,
    kind: str = "cyclic",
    interval: str = "",
    interval_unit: str = "ms",
    priority: str = "1",
    watchdog_enabled: bool = False,
    watchdog_time: str = "",
    pous: list | None = None,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if not name:
        raise ToolError("name (task name) required")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "task create refused - it modifies the project; "
                "call again with confirm=true only on an explicit "
                "owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {
        "op": "create_task",
        "name": name,
        "kind": kind,
        "confirm": True,
    }
    if application:
        args["application"] = application
    if interval:
        args["interval"] = interval
    if interval_unit:
        args["interval_unit"] = interval_unit
    if priority:
        args["priority"] = priority
    args["watchdog_enabled"] = bool(watchdog_enabled)
    if watchdog_time:
        args["watchdog_time"] = watchdog_time
    if pous:
        args["pous"] = [str(p) for p in pous]
    return json.dumps(_run("task_config", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="Full symbol inventory of the application's symbol "
    "configuration: signatures and variables with types and "
    "effective access (get_all_signatures; compile=true "
    "BUILDS first - takes time but is non-destructive). The "
    "symbol configuration object must exist (ecockpit_"
    "symbol_config_create).",
)
async def ecockpit_symbol_config_variables(
    application: str = "",
    compile: bool = True,
    timeout_s: int = 900,
    ctx: Context | None = None,
) -> str:
    args: dict[str, object] = {"compile": bool(compile)}
    if application:
        args["application"] = application
    return json.dumps(await _run_async("symbol_config", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Add the "
    "symbol configuration object to the application "
    "(export_comments, support_opc_ua). Modifies the "
    "project. Pass confirm=true, never without an explicit "
    "owner instruction.",
)
def ecockpit_symbol_config_create(
    application: str = "",
    export_comments: bool = False,
    support_opc_ua: bool = True,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "symbol_config_create refused - it modifies the "
                "project; call again with confirm=true only on an "
                "explicit owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {"op": "create", "confirm": True}
    if application:
        args["application"] = application
    return json.dumps(_run("symbol_config", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="Symbol configuration options + XML schema. op=config "
    "(read-only): content feature flags (decoded + "
    "effective), attribute/comment filter types + data, "
    "direct-IO access + obstacles. op=config_set "
    "(DESTRUCTIVE - requires explicit owner request + "
    "confirm; modifies the project): "
    "content_feature_flags ('+'-combined names: "
    "SupportOPCUA, IncludeComments, IncludeAttributes, "
    "IncludeTypeNodeAttributes, IncludeExecutables, "
    "UseEmptyNamespaceByDefault, XmlInclude*), "
    "attribute_filter_type (none|All|SimpleIdentifiers|"
    "Prefix|Regex) + attribute_filter_data, "
    "comment_filter_type (none|NormalComments|DocuComments|"
    "Both|PreferNormalComments|PreferDocuComments); "
    "read-back included. op=xsd {file?} (read-only): write "
    "the symbol configuration XML schema for THIS release "
    "to a file (default <ECP_PROJECT_ROOT>/plcopensrc-export/"
    "Symbolconfiguration.xsd). The symbol configuration "
    "object must exist (ecockpit_symbol_config_create).",
)
async def ecockpit_symbol_config_options(
    op: str = "config",
    application: str = "",
    content_feature_flags: str = "",
    attribute_filter_type: str = "",
    attribute_filter_data: str = "",
    comment_filter_type: str = "",
    file: str = "",
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if op not in ("config", "config_set", "xsd"):
        raise ToolError("op must be config|config_set|xsd")
    if op == "config_set":
        if not confirm:
            return json.dumps(
                {
                    "ok": False,
                    "refused": True,
                    "error": "symbol_config config_set refused - it modifies "
                    "the project; call again with confirm=true only "
                    "on an explicit owner request",
                },
                indent=2,
            )
        if not (
            content_feature_flags
            or attribute_filter_type
            or attribute_filter_data
            or comment_filter_type
        ):
            raise ToolError(
                "config_set needs content_feature_flags "
                "and/or attribute_filter_type(+data) / "
                "comment_filter_type"
            )
    args: dict[str, object] = {"op": op}
    if application:
        args["application"] = application
    if op == "config_set":
        if content_feature_flags:
            args["content_feature_flags"] = content_feature_flags
        if attribute_filter_type:
            args["attribute_filter_type"] = attribute_filter_type
        if attribute_filter_data:
            args["attribute_filter_data"] = attribute_filter_data
        if comment_filter_type:
            args["comment_filter_type"] = comment_filter_type
        args["confirm"] = True
    if op == "xsd" and file:
        args["file"] = file
    return json.dumps(await _run_async("symbol_config", args, timeout_s, ctx), indent=2)


# --------------------------------------------------------------------------
# Device domain: scan / communication / parameters
# --------------------------------------------------------------------------


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="Gateway + network device discovery. op=gateways lists "
    "configured gateways and drivers; op=scan performs a "
    "network scan on a gateway (blocks for the scan "
    "timeout) and returns discovered targets (name, "
    "address, vendor, id, version); op=address resolves a "
    "CODESYS address from an IP via find_address_by_ip. "
    "Read-only.",
)
async def ecockpit_device_scan(
    op: str = "gateways",
    gateway: str = "",
    ip: str = "",
    port: int = 11740,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if op not in ("gateways", "scan", "address"):
        raise ToolError("op must be gateways|scan|address")
    if op == "address" and not ip:
        raise ToolError("ip required for op=address")
    args: dict[str, object] = {"op": op}
    if gateway:
        args["gateway"] = gateway
    if op == "address":
        args["ip"] = ip
        args["port"] = int(port)
    return json.dumps(await _run_async("device_scan", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="Device communication settings. op=get (read-only): "
    "gateway guid, address, scanned_* values from the last "
    "network scan. op=set_ip (DESTRUCTIVE - requires "
    "explicit owner request + confirm): point the device at "
    "an IP via a gateway (modifies the project device "
    "configuration).",
)
def ecockpit_device_comm(
    op: str = "get",
    device: str = "",
    gateway: str = "",
    ip: str = "",
    device_name: str = "",
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if op not in ("get", "set_ip"):
        raise ToolError("op must be get|set_ip")
    if op == "set_ip":
        if not ip:
            raise ToolError("ip required for op=set_ip")
        if not confirm:
            return json.dumps(
                {
                    "ok": False,
                    "refused": True,
                    "error": "device_comm set_ip refused - it modifies the "
                    "project device configuration; call again with "
                    "confirm=true only on an explicit owner request",
                },
                indent=2,
            )
    args: dict[str, object] = {"op": op}
    if device:
        args["device"] = device
    if op == "set_ip":
        args["ip"] = ip
        args["confirm"] = True
        if gateway:
            args["gateway"] = gateway
        if device_name:
            args["device_name"] = device_name
    return json.dumps(_run("device_comm", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="Device identification / simulation / driver info (the "
    "visible parts of the PLC Settings tab). op="
    "identification (read-only): DeviceId {type, id, "
    "version} + module identification. op=simulation "
    "(read-only) / op=simulation_set {enabled, confirm - "
    "DESTRUCTIVE: changes the device's simulation flag in "
    "the project}. op=driver_info (read-only): bus cycle "
    "task, IO application, update-while-stop, outputs-on-"
    "stop behaviour, always-update mode, force-variable "
    "generation, diagnosis. op=driver_info_set (DESTRUCTIVE "
    "- requires explicit owner request + confirm): "
    "bus_cycle_task, io_application (application name), "
    "update_ios_while_in_stop, behaviour_for_outputs_on_stop "
    "(KeepCurrentValues|SetToDefault|ExecuteProgram), "
    "user_program_for_stop_reset_behaviour, "
    "always_update_variables (Disabled|OnlyIfUnused|"
    "AlwaysInBusCycle), generate_force_variables, "
    "enable_diagnosis, show_io_warnings_as_errors; "
    "read-back included.",
)
def ecockpit_device_info(
    op: str = "identification",
    device: str = "",
    enabled: bool = False,
    bus_cycle_task: str = "",
    io_application: str = "",
    update_ios_while_in_stop: bool | None = None,
    behaviour_for_outputs_on_stop: str = "",
    user_program_for_stop_reset_behaviour: str = "",
    always_update_variables: str = "",
    generate_force_variables: bool | None = None,
    enable_diagnosis: bool | None = None,
    show_io_warnings_as_errors: bool | None = None,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if op not in (
        "identification",
        "simulation",
        "simulation_set",
        "driver_info",
        "driver_info_set",
    ):
        raise ToolError(
            "op must be identification|simulation|"
            "simulation_set|driver_info|driver_info_set"
        )
    if op in ("simulation_set", "driver_info_set") and not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "device_info {o} refused - it modifies the project "
                "device configuration; call again with confirm=true "
                "only on an explicit owner request".format(o=op),
            },
            indent=2,
        )
    args: dict[str, object] = {"op": op}
    if device:
        args["device"] = device
    if op == "simulation_set":
        args["enabled"] = bool(enabled)
        args["confirm"] = True
    if op == "driver_info_set":
        args["confirm"] = True
        if bus_cycle_task:
            args["bus_cycle_task"] = bus_cycle_task
        if io_application:
            args["io_application"] = io_application
        if update_ios_while_in_stop is not None:
            args["update_ios_while_in_stop"] = bool(update_ios_while_in_stop)
        if behaviour_for_outputs_on_stop:
            args["behaviour_for_outputs_on_stop"] = behaviour_for_outputs_on_stop
        if user_program_for_stop_reset_behaviour:
            args["user_program_for_stop_reset_behaviour"] = (
                user_program_for_stop_reset_behaviour
            )
        if always_update_variables:
            args["always_update_variables"] = always_update_variables
        if generate_force_variables is not None:
            args["generate_force_variables"] = bool(generate_force_variables)
        if enable_diagnosis is not None:
            args["enable_diagnosis"] = bool(enable_diagnosis)
        if show_io_warnings_as_errors is not None:
            args["show_io_warnings_as_errors"] = bool(show_io_warnings_as_errors)
    return json.dumps(_run("device_info", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="IO mapping inspection + CSV round-trip. op=mappings "
    "(read-only): every mappable parameter element with its "
    "io_mapping (variable, default_variable, "
    "mapping_creates_variable, maps_to_existing_variable, "
    "automatic_iec_address, manual_iec_address); "
    "connector_id scopes to one connector. op=set_mapping "
    "(DESTRUCTIVE - requires explicit owner request + "
    "confirm): set variable and/or manual_iec_address "
    "(automatic=true resets to the automatic address) on a "
    "parameter's element - element=<identifier> targets one "
    "channel; multi-element parameters REFUSE without it "
    "unless all_elements=true (G-21). "
    "op=export_csv {file?}: export the device's IO mappings "
    "to CSV (default <ECP_PROJECT_ROOT>/io-mappings/"
    "<device>_io_mappings.csv). op=import_csv {file, "
    "confirm}: DESTRUCTIVE - import IO mappings from a CSV "
    "(modifies the IO configuration).",
)
async def ecockpit_device_io(
    op: str = "mappings",
    device: str = "",
    connector_id: int | None = None,
    name: str = "",
    id: int | None = None,
    element: str = "",
    variable: str = "",
    manual_iec_address: str = "",
    automatic: bool = False,
    all_elements: bool = False,
    file: str = "",
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if op not in ("mappings", "set_mapping", "export_csv", "import_csv"):
        raise ToolError("op must be mappings|set_mapping|export_csv|import_csv")
    if op in ("set_mapping", "import_csv") and not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "device_io {o} refused - it modifies the IO "
                "configuration; call again with confirm=true only "
                "on an explicit owner request".format(o=op),
            },
            indent=2,
        )
    args: dict[str, object] = {"op": op}
    if device:
        args["device"] = device
    if connector_id is not None:
        args["connector_id"] = int(connector_id)
    if op == "set_mapping":
        if not name and id is None:
            raise ToolError("name or id required for op=set_mapping")
        if not variable and not manual_iec_address and not automatic:
            raise ToolError(
                "variable and/or manual_iec_address/"
                "automatic required for op=set_mapping"
            )
        if name:
            args["name"] = name
        if id is not None:
            args["id"] = int(id)
        if element:
            args["element"] = element
        if variable:
            args["variable"] = variable
        if manual_iec_address:
            args["manual_iec_address"] = manual_iec_address
        if automatic:
            args["automatic"] = True
        if all_elements:
            args["all_elements"] = True
        args["confirm"] = True
    if op == "export_csv" and file:
        args["file"] = file
    if op == "import_csv":
        if not file:
            raise ToolError("file required for op=import_csv")
        args["file"] = file
    return json.dumps(await _run_async("device_io", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="Device parameter access. op=list: parameter inventory "
    "(id, name, type, section). op=read: parameter value - "
    "offline (default) or online=true to read from the PLC. "
    "op=write: DESTRUCTIVE - ONLINE PARAMETER WRITE to the "
    "device; enums are validated against allowed values; "
    "struct parameters take element. Write requires "
    "confirm=true on an explicit owner request.",
)
def ecockpit_device_params(
    op: str = "list",
    device: str = "",
    name: str = "",
    id: int | None = None,
    connector_id: int | None = None,
    element: str = "",
    value: str = "",
    online: bool = False,
    timeout: int = 1000,
    confirm: bool = False,
    timeout_s: int = 180,
) -> str:
    if op not in ("list", "read", "write"):
        raise ToolError("op must be list|read|write")
    args: dict[str, object] = {"op": op, "timeout": int(timeout)}
    if device:
        args["device"] = device
    if connector_id is not None:
        args["connector_id"] = int(connector_id)
    if op in ("read", "write"):
        if not name and id is None:
            raise ToolError("name or id required")
        if name:
            args["name"] = name
        if id is not None:
            args["id"] = int(id)
    if op == "read" and online:
        args["online"] = True
    if op == "write":
        if value == "":
            raise ToolError("value required for op=write")
        if not confirm:
            return json.dumps(
                {
                    "ok": False,
                    "refused": True,
                    "error": "device_params write refused - it writes a "
                    "parameter to the DEVICE; call again with "
                    "confirm=true only on an explicit owner request",
                },
                indent=2,
            )
        args["value"] = value
        args["confirm"] = True
        if element:
            args["element"] = element
    return json.dumps(_run("device_params", args, timeout_s), indent=2)


# --------------------------------------------------------------------------
# Application + project lifecycle
# --------------------------------------------------------------------------


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="Application status (read-only): is_active, is_uptodate "
    "(matches what runs on the PLC) and "
    "is_online_change_possible.",
)
def ecockpit_app_status(application: str = "", timeout_s: int = 120) -> str:
    args: dict[str, object] = {"op": "status"}
    if application:
        args["application"] = application
    return json.dumps(_run("app_control", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. op="
    "activate: make this application the active one. op="
    "boot: write an OFFLINE boot application file on this "
    "host (NOT a PLC download). op=start: start the program "
    "on the RUNNING PLC. op=reset: reset the PLC - option "
    "warm|cold|original, where original DELETES all "
    "applications, boot project and retain data. All ops "
    "modify or touch the PLC/project; pass confirm=true, "
    "never without an explicit owner instruction.",
)
async def ecockpit_app_control(
    op: str,
    application: str = "",
    path: str = "",
    reset_option: str = "warm",
    force_kill: bool = False,
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if op not in ("status", "activate", "boot", "start", "reset"):
        raise ToolError("op must be status|activate|boot|start|reset")
    if op != "status" and not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "app_control {o} refused - it touches the project "
                "or the RUNNING PLC; call again with confirm=true "
                "only on an explicit owner request".format(o=op),
            },
            indent=2,
        )
    args: dict[str, object] = {"op": op, "confirm": True}
    if application:
        args["application"] = application
    if op == "boot":
        if not path:
            raise ToolError("path (host .bootapp file) required for op=boot")
        args["path"] = path
    if op == "reset":
        args["option"] = reset_option
        args["force_kill"] = bool(force_kill)
    return json.dumps(await _run_async("app_control", args, timeout_s, ctx), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="Project lifecycle + info. op=info and op=save are "
    "safe. DESTRUCTIVE - requires explicit owner request + "
    "confirm: op=create (NEW project, becomes primary), "
    "op=open (opens a project as primary), op=open_archive "
    "(restore a .projectarchive), op=close (DISCARDS "
    "unsaved changes), op=info_set (project metadata). "
    "These swap or mutate the GUI's project session.",
)
async def ecockpit_project_control(
    op: str,
    path: str = "",
    archive: str = "",
    project_path: str = "",
    overwrite: bool = False,
    company: str = "",
    title: str = "",
    version: str = "",
    author: str = "",
    description: str = "",
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if op not in (
        "info",
        "info_set",
        "save",
        "create",
        "open",
        "open_archive",
        "close",
    ):
        raise ToolError("op must be info|info_set|save|create|open|open_archive|close")
    gated = op in ("info_set", "create", "open", "open_archive", "close")
    if gated and not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "project {o} refused - it mutates the GUI project "
                "session or project metadata; call again with "
                "confirm=true only on an explicit owner "
                "request".format(o=op),
            },
            indent=2,
        )
    args: dict[str, object] = {"op": op}
    if gated:
        args["confirm"] = True
    if op == "create" or op == "open":
        if not path:
            raise ToolError("path required for op=" + op)
        args["path"] = path
    if op == "open_archive":
        if not archive or not project_path:
            raise ToolError("archive and project_path required for op=open_archive")
        args["archive"] = archive
        args["project_path"] = project_path
        args["overwrite"] = bool(overwrite)
    if op == "info_set":
        for key in ("company", "title", "version", "author", "description"):
            value = {
                "company": company,
                "title": title,
                "version": version,
                "author": author,
                "description": description,
            }.get(key, "")
            if value:
                args[key] = value
    return json.dumps(
        await _run_async("project_control", args, timeout_s, ctx), indent=2
    )


# --------------------------------------------------------------------------
# PLC file system: manipulation ops (ls/get/put/rm have their own tools)
# --------------------------------------------------------------------------


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="PLC file system operations on the ScriptOnlineDevice. "
    "op=ls is READ-ONLY (list a directory; the runtime root "
    "is '.' — '/' is refused, the result reports the probed "
    "directory). DESTRUCTIVE - requires explicit owner "
    "request + confirm: op=rename (remote + new_name), "
    "op=mkdir (remote), op=rmdir (remote; recursive=true "
    "deletes recursively). ls needs no confirm; the others "
    "never without an explicit owner instruction.",
)
async def ecockpit_plc_fs_ops(
    op: str,
    remote: str,
    new_name: str = "",
    recursive: bool = False,
    device: str = "",
    confirm: bool = False,
    timeout_s: int = 300,
    ctx: Context | None = None,
) -> str:
    if op not in ("ls", "rename", "mkdir", "rmdir"):
        raise ToolError("op must be ls|rename|mkdir|rmdir")
    if not remote:
        raise ToolError("remote (PLC path) required")
    if op == "rename" and not new_name:
        raise ToolError("new_name required for op=rename")
    if op != "ls" and not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "plc_fs_ops {o} refused - it modifies the PLC file "
                "system; call again with confirm=true only on an "
                "explicit owner request".format(o=op),
            },
            indent=2,
        )
    args: dict[str, object] = {"op": op, "remote": remote}
    if op != "ls":
        args["confirm"] = True
    if op == "rename":
        args["new_name"] = new_name
    if op == "rmdir":
        args["recursive"] = bool(recursive)
    if device:
        args["device"] = device
    return json.dumps(await _run_async("plcfs", args, timeout_s, ctx), indent=2)


# --------------------------------------------------------------------------
# MCP resources + prompts (protocol surface beyond tools)
# --------------------------------------------------------------------------
async def _resource_payload(cmd, args):
    """Run one read-only command for a resource read. Never raises: an
    unarmed relay or a dead engine reads back an ok:false payload with
    the rearm hint instead of a protocol error, so agents can discover
    state without exception handling."""
    try:
        result = await _run_async(cmd, args)
    except NotArmedError:
        return json.dumps(
            {"ok": False, "error": "relay not armed", "hint": REARM_HINT}, indent=2
        )
    except (TimeoutError, OSError) as exc:
        return json.dumps(
            {
                "ok": False,
                "error": "engine unreachable: {e}".format(e=exc)[:200],
                "hint": REARM_HINT,
            },
            indent=2,
        )
    return json.dumps(result, indent=2)


@mcp.resource(
    "ecockpit://project/tree",
    name="project-tree",
    mime_type="application/json",
    description="The open e!COCKPIT project tree as JSON: every object "
    "with name, GUID and kind markers (device/application/folder/symbol "
    "config/textual flags), recursive, limit 1000. Requires the armed "
    "command relay; an unarmed relay reads back an ok:false payload with "
    "the rearm hint.",
)
async def ecockpit_resource_project_tree() -> str:
    return await _resource_payload("tree", {"recursive": True, "limit": 1000})


@mcp.resource(
    "ecockpit://messages",
    name="compiler-messages",
    mime_type="application/json",
    description="The e!COCKPIT scripting message store as JSON: the last "
    "build/compile diagnostics plus the active IGNORE_CODES filter and "
    "category map (the data behind the harvest in build results). "
    "Requires the armed command relay; an unarmed relay reads back an "
    "ok:false payload with the rearm hint.",
)
async def ecockpit_resource_messages() -> str:
    return await _resource_payload("messages", {})


@mcp.resource(
    "ecockpit://project/textual/{name}",
    name="project-textual",
    mime_type="application/json",
    description="ST source of one project object as JSON: the "
    "declaration and implementation text of a textual object (POU, GVL, "
    "DUT, ...), read via ScriptTextDocument. {name} is the project-tree "
    "object name; requires the armed command relay.",
)
async def ecockpit_resource_textual(name: str) -> str:
    return await _resource_payload("textual", {"op": "read", "name": name})


@mcp.prompt(
    name="diagnose-failed-build",
    description="Structured diagnosis pass for a failed ecockpit_build / "
    "ecockpit_compile / ecockpit_rebuild result. Paste the tool's JSON "
    "output as build_result (or leave empty for the generic checklist).",
)
def diagnose_failed_build(build_result: str = "") -> list:
    checklist = (
        "Diagnose this e!COCKPIT build failure step by step:\n"
        "1. Parse the structured harvest (C-code, severity, text, POU, "
        "line). Group the entries by C-code before reading anything "
        "else - one root cause usually repeats.\n"
        "2. Check the IGNORE_CODES filter: the result was filtered by "
        "default. Re-run with all_messages=true (ecockpit_compile or "
        "ecockpit_build) before concluding an error list is complete - "
        "a filtered C-code can still be the real cause.\n"
        "3. C0106 'out of global data memory' on grown GVL arrays is an "
        "incremental-build artifact: use ecockpit_rebuild (scripted "
        "Build -> Clean), not another compile.\n"
        "4. Empty harvest but ok=false: the failure is NOT a compile "
        "error. Read log_tail for the import step (XML parse, "
        "REMOVED_POUS) and check ecockpit_messages for engine-level "
        "messages; a stale message store hides causes - "
        "ecockpit_clear_messages, then compile again.\n"
        "5. For errors in a specific POU, read the ST source "
        "(ecockpit://project/textual/{name} resource or "
        "ecockpit_textual_read), fix with ecockpit_textual_write / "
        "ecockpit_object_create (both confirm-gated - owner request "
        "first), then rebuild.\n"
        "6. Report: root-cause C-codes, the POUs/lines involved, whether "
        "IGNORE_CODES filtered anything, and the exact next command you "
        "recommend - never a broad 'rebuild everything' on a running "
        "PLC project without saying what it costs.\n"
    )
    if build_result.strip():
        user_text = checklist + "\nFailed build result (JSON):\n" + build_result
    else:
        user_text = (
            checklist + "\nNo build result was pasted. Run ecockpit_build (or "
            "ecockpit_compile with all_messages=true) first, then apply "
            "this checklist to its JSON output."
        )
    return [{"role": "user", "content": user_text}]


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
            "(env or .env) or pass --token".format(host)
        )
    if not token:
        print(
            "[ecockpit-mcp] tokenless loopback bind - set ECP_MCP_TOKEN "
            "to require bearer auth",
            flush=True,
        )

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
                    await send(
                        {
                            "type": "http.response.start",
                            "status": 401,
                            "headers": [(b"content-type", b"application/json")],
                        }
                    )
                    await send(
                        {
                            "type": "http.response.body",
                            "body": b'{"error":"unauthorized - missing or '
                            b'invalid bearer token"}',
                        }
                    )
                    return
            await self.inner(scope, receive, send)

    import uvicorn

    print(
        "[ecockpit-mcp] streamable-http on http://{h}:{p}/mcp (token: {t})".format(
            h=host, p=CFG.port, t="required" if token else "DISABLED (loopback)"
        ),
        flush=True,
    )
    uvicorn.run(
        BearerTokenMiddleware(app), host=host, port=CFG.port, log_level="warning"
    )


# --------------------------------------------------------------------------
# Library curation (global `librarymanager` / ILibManager)
# --------------------------------------------------------------------------


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
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
    "carries total/offset/count/truncated for walking pages.",
)
def ecockpit_libs(
    repo: str = "",
    shadowed: bool = False,
    filter: str = "",
    offset: int = 0,
    limit: int = 200,
    timeout_s: int = 120,
) -> str:
    args: dict[str, object] = {}
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
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="Find a library by display name or title. Searches "
    "repositories in their configured order and returns every "
    "match with its repository, file path and a shadowed flag "
    "(true when an earlier repository in search order has the "
    "same library - the first match wins). exact=true matches "
    "the full display name/title only; the default also "
    "accepts the bare title ('NETWORK' finds 'NETWORK, 1.3.5.3 "
    "(OSCAT)'). repo narrows the search.",
)
def ecockpit_lib_find(
    name: str, repo: str = "", exact: bool = False, timeout_s: int = 120
) -> str:
    if not name:
        raise ToolError("name (library name or display name) required")
    args: dict[str, object] = {"name": name}
    if repo:
        args["repo"] = repo
    if exact:
        args["exact"] = True
    return json.dumps(_run("lib_find", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=False, open_world_hint=True
    ),
    description="Install a compiled-library file (e.g. .library) into a "
    "library repository. repo names the target repository "
    "(default: the FIRST repository in the search order). "
    "overwrite=true replaces an existing library and requires "
    "confirm=true (as does any replacement - call with "
    "confirm only on an explicit owner request). Modifies the "
    "machine-wide library repositories, not the open project.",
)
def ecockpit_lib_install(
    path: str,
    repo: str = "",
    overwrite: bool = False,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if not path:
        raise ToolError("path (compiled-library file) required")
    if overwrite and not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "lib_install refused - overwrite=true replaces an "
                "existing library; call again with confirm=true only "
                "on an explicit owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {"path": path, "overwrite": bool(overwrite)}
    if repo:
        args["repo"] = repo
    if overwrite:
        args["confirm"] = True
    return json.dumps(_run("lib_install", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Uninstall "
    "a library from a library repository (removes the files "
    "from the repository; projects referencing it will stop "
    "resolving). If the library exists in several "
    "repositories the command is refused unless repo names "
    "one. Pass confirm=true, never without an explicit owner "
    "instruction.",
)
def ecockpit_lib_uninstall(
    name: str, repo: str = "", confirm: bool = False, timeout_s: int = 120
) -> str:
    if not name:
        raise ToolError("name (library name or display name) required")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "lib_uninstall refused - it removes a library from a "
                "repository; call again with confirm=true only on an "
                "explicit owner request",
            },
            indent=2,
        )
    args: dict[str, object] = {"name": name, "confirm": True}
    if repo:
        args["repo"] = repo
    return json.dumps(_run("lib_uninstall", args, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=False, read_only_hint=True, open_world_hint=True
    ),
    description="List the library repositories in their configured search "
    "order (first match wins when resolving libraries): "
    "name, root folder, editable flag (system repositories "
    "are not), and per-repository library count.",
)
def ecockpit_repos(timeout_s: int = 120) -> str:
    return json.dumps(_run("repos", {}, timeout_s), indent=2)


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Register "
    "a NEW library repository (machine-wide change). "
    "rootfolder must already exist on disk; index places it "
    "in the search order (-1 = append at the end). Pass "
    "confirm=true, never without an explicit owner "
    "instruction.",
)
def ecockpit_repo_add(
    rootfolder: str,
    name: str,
    index: int = -1,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if not rootfolder or not name:
        raise ToolError("rootfolder (existing directory) and name required")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "repo_add refused - it changes the machine-wide "
                "library repository list; call again with "
                "confirm=true only on an explicit owner request",
            },
            indent=2,
        )
    return json.dumps(
        _run(
            "repo_add",
            {
                "rootfolder": rootfolder,
                "name": name,
                "index": int(index),
                "confirm": True,
            },
            timeout_s,
        ),
        indent=2,
    )


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Unregister "
    "a library repository. delete_on_disk=true ALSO DELETES "
    "the repository directory with all its library files - "
    "only on an explicit owner instruction. System "
    "repositories are not removable. Pass confirm=true, never "
    "without an explicit owner instruction.",
)
def ecockpit_repo_remove(
    name: str, delete_on_disk: bool = False, confirm: bool = False, timeout_s: int = 120
) -> str:
    if not name:
        raise ToolError("name (repository name) required")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "repo_remove refused - it changes the machine-wide "
                "library repository list (delete_on_disk=true also "
                "deletes the directory); call again with confirm=true "
                "only on an explicit owner request",
            },
            indent=2,
        )
    return json.dumps(
        _run(
            "repo_remove",
            {"name": name, "delete_on_disk": bool(delete_on_disk), "confirm": True},
            timeout_s,
        ),
        indent=2,
    )


@mcp.tool(
    structured_output=False,
    annotations=ToolAnnotations(
        destructive_hint=True, read_only_hint=False, open_world_hint=True
    ),
    description="DESTRUCTIVE - requires explicit owner request. Rename a "
    "library repository (new_name) and/or move it to "
    "new_location (must exist; copy_libraries=true installs "
    "the old location's libraries at the new one when "
    "missing). Pass confirm=true, never without an explicit "
    "owner instruction.",
)
def ecockpit_repo_update(
    name: str,
    new_name: str = "",
    new_location: str = "",
    copy_libraries: bool = False,
    confirm: bool = False,
    timeout_s: int = 120,
) -> str:
    if not name:
        raise ToolError("name (repository name) required")
    if not new_name and not new_location:
        raise ToolError("new_name and/or new_location required")
    if not confirm:
        return json.dumps(
            {
                "ok": False,
                "refused": True,
                "error": "repo_update refused - it changes the machine-wide "
                "library repository list; call again with "
                "confirm=true only on an explicit owner request",
            },
            indent=2,
        )
    return json.dumps(
        _run(
            "repo_update",
            {
                "name": name,
                "new_name": new_name,
                "new_location": new_location,
                "copy_libraries": bool(copy_libraries),
                "confirm": True,
            },
            timeout_s,
        ),
        indent=2,
    )


@mcp.tool(
    structured_output=False,
    description="Reorder the library repository search order (first "
    "repository wins when a library name resolves to several "
    "ones). index is the new position, -1 moves the "
    "repository to the end. Reversible settings change - no "
    "confirm needed."
)
def ecockpit_repo_move(name: str, index: int, timeout_s: int = 120) -> str:
    if not name:
        raise ToolError("name (repository name) required")
    return json.dumps(
        _run("repo_move", {"name": name, "index": int(index)}, timeout_s), indent=2
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--transport",
        choices=("http", "stdio"),
        default=None,
        help="http (default) or stdio (client-spawned)",
    )
    parser.add_argument(
        "--work-dir",
        default=None,
        help="file-protocol work dir (default: tmp/ next "
        "to this script, or ECP_WORK_DIR)",
    )
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument(
        "--token",
        default=None,
        help="bearer token for http; required for non-loopback binds",
    )
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
            "server)".format(e=executor)
        )

    print("[ecockpit-mcp] work dir: {r}".format(r=CFG.tmp_dir), flush=True)
    if CFG.transport == "stdio":
        # No print() after this - stdout carries the MCP protocol.
        mcp.run(transport="stdio")
    else:
        _run_http()


if __name__ == "__main__":
    main()
