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
    ecockpit_cmd_server.py (IronPython INSIDE e!COCKPIT, armed via
        |                   FILE -> Scripting -> Execute script file)
        v  exec per command
    ecockpit_exec.py (bundled executor; drives the open project via the
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
    .venv/Scripts/python.exe ecockpit_mcp_server.py --transport http
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
_DOTENV = _load_dotenv(os.path.join(PROJECT_DIR, ".env"))


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
        # File-protocol work dir - shared with ecockpit_cmd_server.py /
        # ecockpit_exec.py (which resolve the same ECP_WORK_DIR/.env key).
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
    "status": 120,
    "read": 180,
    "download": 600,
    "disconnect": 120,
    "probe": 300,
    "messages": 300,
    "snapshot": 300,
}
_DEFAULT_TIMEOUT = 300

_SEND_LOCK = threading.Lock()

REARM_HINT = ("e!COCKPIT command server is not running. Arm it in the open "
              "e!COCKPIT GUI: FILE -> Scripting -> Execute script file -> "
              "ecockpit_cmd_server.py (from this ecockpit-mcp checkout)")


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
        "instance via its command server - it must be armed (FILE -> "
        "Scripting -> Execute script file -> ecockpit_cmd_server.py). "
        "Build results include the structured compiler diagnostics "
        "(harvest: C-code, severity, POU, line). ecockpit_download replaces "
        "the RUNNING application on the live PLC - only call it on an "
        "explicit owner request, with confirm=true."))


@mcp.tool(
    description="Check whether the in-instance e!COCKPIT command server is "
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
                "filter for this run.")
def ecockpit_build(skip_import: bool = False, all_messages: bool = False,
                   timeout_s: int = 900) -> str:
    return json.dumps(_run("build", {"skip_import": bool(skip_import),
                                     "all_messages": bool(all_messages)},
                           timeout_s), indent=2)


@mcp.tool(
    description="Re-import every Application.*.xml into the open e!COCKPIT "
                "project and delete stale POUs - no compile. Takes ~15-30 s.")
def ecockpit_import(timeout_s: int = 600) -> str:
    return json.dumps(_run("import", {}, timeout_s), indent=2)


@mcp.tool(
    description="Compile only (no XML re-import). Same diagnostics and "
                "IGNORE_CODES filtering as build (all_messages=true "
                "bypasses).")
def ecockpit_compile(all_messages: bool = False, timeout_s: int = 900) -> str:
    return json.dumps(_run("compile", {"all_messages": bool(all_messages)},
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


@mcp.tool(
    description="Dump the message-store category map (scripting API "
                "debugging aid) and the active IGNORE_CODES filter list.")
def ecockpit_messages() -> str:
    return json.dumps(_run("messages", {}), indent=2)


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
                "imports).")
def ecockpit_disconnect() -> str:
    return json.dumps(_run("disconnect", {}), indent=2)


@mcp.tool(
    description="Dump the scripting API surface (dir() + getattr probes) to "
                "tmp/logs/ecockpit_api_probe.txt for API discovery.")
def ecockpit_probe() -> str:
    return json.dumps(_run("probe", {}), indent=2)


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

    executor = os.path.join(PROJECT_DIR, "ecockpit_exec.py")
    if not os.path.isfile(executor):
        raise SystemExit(
            "bundled executor missing: {e} - keep the ecockpit-mcp "
            "checkout intact (ecockpit_exec.py next to this "
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
