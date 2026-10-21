# -*- coding: utf-8 -*-
# This comment is required if there are any non-ASCII characters in the script file.
"""
eCockpit COMMAND SERVER - runs inside an ALREADY-OPEN e!COCKPIT instance.

This script is the in-instance half of the ecockpit-mcp project. Arm it
once per e!COCKPIT session:

    FILE -> Scripting -> Execute script file -> ecockpit_cmd_server.py

It polls a command file (default <work dir>/ecockpit_cmd.json; the work dir
is tmp/ next to this script, overridable via ECP_WORK_DIR), executes each
command against the open project by re-running the executor
(ecockpit_exec.py, bundled next to this script), and writes id-matched
results. A heartbeat file lets clients distinguish "not armed" from "busy".

CONFIGURATION
-------------
Everything is read from the .env NEXT TO THIS SCRIPT (the ecockpit-mcp
.env - one place configures both halves): ECP_WORK_DIR / ECP_WATCH_POLL
here, ECP_PROJECT_ROOT and friends for the executor. Environment
variables win over the .env.

Stop: create <work dir>/ecockpit_server.stop, or use e!COCKPIT's
"Stop script" command. On startup it also writes the previous-generation
watcher stop file (build_watch.stop) so at most one server polls.
"""

import os
import sys
import time
import json
import traceback


# e!COCKPIT's ScriptEngine injects system/Severity when this script is armed
# inside e!COCKPIT. The guarded form keeps the injected originals there and
# binds stand-ins under plain CPython (flake8, offline import) so the module
# stays lintable. run_command is bound by exec()ing ecockpit_exec.py into
# this module's globals once per command (see _dispatch).
if "system" not in globals():
    system = None
if "Severity" not in globals():
    Severity = None
run_command = None


# ---------------------------------------------------------------------------
# USER CONFIGURATION
# ---------------------------------------------------------------------------
# Work dir resolution order (holds the command/result/heartbeat files):
#   1. ECP_WORK_DIR environment variable
#   2. ECP_WORK_DIR in the .env file NEXT TO THIS SCRIPT (the ecockpit-mcp
#      project .env - one place configures both halves)
#   3. tmp/ next to this script

def _env_file_value(key):
    try:
        base = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base, ".env"), "r") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith(key + "="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))

WORK_DIR = os.environ.get("ECP_WORK_DIR", "") \
    or _env_file_value("ECP_WORK_DIR") \
    or os.path.join(SCRIPT_DIR, "tmp")

CMD_FILE        = os.path.join(WORK_DIR, "ecockpit_cmd.json")
RESULT_FILE     = os.path.join(WORK_DIR, "ecockpit_cmd_result.json")
LEGACY_TRIGGER  = os.path.join(WORK_DIR, "build_trigger")
OLD_WATCH_STOP  = os.path.join(WORK_DIR, "build_watch.stop")
STOP_FILE       = os.path.join(WORK_DIR, "ecockpit_server.stop")
HEARTBEAT_FILE  = os.path.join(WORK_DIR, "ecockpit_watch.heartbeat")

# The executor is bundled with this script (same directory).
EXEC_SCRIPT     = os.path.join(SCRIPT_DIR, "ecockpit_exec.py")


def _raw_args():
    try:
        s = system.script_arguments
    except Exception:
        s = ""
    if isinstance(s, str) and s.strip():
        return s.strip()
    if isinstance(s, (list, tuple)) and s:
        return " ".join(str(x) for x in s)
    return ""


def _opt(name, env, default):
    """Read a --name value from scriptargs, else env, else default."""
    import shlex
    raw = _raw_args()
    try:
        toks = shlex.split(raw, posix=False)
    except Exception:
        toks = raw.split()
    key = "--" + name
    for i, t in enumerate(toks):
        if t.strip().strip('"').strip("'") == key and i + 1 < len(toks):
            return toks[i + 1].strip().strip('"').strip("'")
    return os.environ.get(env, default)


def _say(msg):
    line = "[server] " + msg
    print(line)
    try:
        system.write_message(Severity.Info, line)
    except Exception:
        pass


def _rm(path):
    try:
        os.remove(path)
    except OSError:
        pass


def _touch(path):
    """Refresh the heartbeat's mtime by actually rewriting its content.

    NOTE: open(path, "ab") + close() WITHOUT writing does NOT update an
    existing file's mtime on NTFS (observed 2026-10-20: the server polled
    fine but the heartbeat looked stale). Always write + truncate instead.
    """
    try:
        if os.path.isfile(path):
            fh = open(path, "r+b")
        else:
            fh = open(path, "wb")
        try:
            fh.write(_to_bytes(str(time.time())))
            fh.truncate()
        finally:
            fh.close()
    except Exception:
        pass


def _to_bytes(s):
    if isinstance(s, bytes):
        return s
    return s.encode("utf-8", "replace")


def _atomic_write_json(path, obj):
    """Write JSON atomically: temp file, then rename over the destination.

    os.rename on Windows fails when the destination exists, so the old file
    is removed first. Single-writer by protocol (one server, one client at
    a time), so the tiny remove/rename window is acceptable.
    """
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    tmp = path + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(_to_bytes(json.dumps(obj)))
    _rm(path)
    os.rename(tmp, path)


def _append_log_line(path, text):
    """Append one line to the build log (crash path: guarantee RESULT=)."""
    try:
        with open(path, "ab") as fh:
            fh.write(_to_bytes(text + "\n"))
    except Exception:
        pass


def _load_request(path):
    """Read + parse the command file. Returns dict, or None (file parked)."""
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
        req = json.loads(raw.decode("utf-8", "replace"))
        if not isinstance(req, dict) or "cmd" not in req:
            raise ValueError("not a command dict")
        return req
    except Exception as exc:
        # Never wedge the loop on a malformed file: park it for inspection.
        park = path + ".bad-{ts}".format(ts=int(time.time()))
        try:
            os.rename(path, park)
        except Exception:
            _rm(path)
        _say("malformed command file parked as {p}: {e}".format(p=park, e=exc))
        return None


def _dispatch(request, counter):
    """Run one command and ALWAYS produce a result file + RESULT= log line."""
    rid = request.get("id", "") or "srv-{n}".format(n=counter)
    request["id"] = rid
    cmd = request.get("cmd", "build")
    _say("command #{n}: {c} (id {i})".format(n=counter, c=cmd, i=rid))
    result = None
    log_file = None
    try:
        # Re-exec the executor so live edits apply without re-arming.
        os.environ["ECP_NO_RUN"] = "1"
        with open(EXEC_SCRIPT, "r") as _bf:
            exec(_bf.read(), globals())
        # Reuse THIS already-open primary project, never a discovered .ecp.
        globals()["PROJECT_FILE"] = ""
        # The executor owns the real log path (REPO_LOG_DIR) - adopt it.
        log_file = globals().get("LOG_FILE")
        result = run_command(request)
    except Exception:
        result = {"id": rid, "cmd": cmd, "ok": False, "value": None,
                  "error": traceback.format_exc(), "log": log_file}
        _say("command #{n} crashed in server:\n{tb}".format(
            n=counter, tb=result["error"]))
    finally:
        if result is None:
            result = {"id": rid, "cmd": cmd, "ok": False, "value": None,
                      "error": "no result produced", "log": log_file}
        # Guarantee the RESULT= line even when the executor never flushed.
        if log_file:
            try:
                with open(log_file, "rb") as fh:
                    ok_marker = b"RESULT=" in fh.read()[-4096:]
            except Exception:
                ok_marker = False
            if not ok_marker:
                _append_log_line(log_file,
                                 "RESULT=" + ("OK" if result.get("ok") else "FAILED"))
        try:
            _atomic_write_json(RESULT_FILE, result)
        except Exception as exc:
            _say("writing result file failed: {e}".format(e=exc))
    _say("command #{n} done: {c} -> {r}".format(
        n=counter, c=cmd, r=("OK" if result.get("ok") else "FAILED")))


def main_server():
    poll_ms = int(_opt("poll", "ECP_WATCH_POLL", "500"))

    if not os.path.isfile(EXEC_SCRIPT):
        _say("CONFIGURATION ERROR: executor not found at {e} - keep "
             "ecockpit_exec.py in the same directory as "
             "ecockpit_cmd_server.py, then re-arm.".format(e=EXEC_SCRIPT))
        try:
            system.write_message(Severity.Error,
                                 "[server] executor missing - see "
                                 "the script header / eCockpit Messages")
        except Exception:
            pass
        return

    # Retire the previous-generation watcher if it is still armed: it would
    # double-execute legacy build triggers.
    try:
        with open(OLD_WATCH_STOP, "wb") as fh:
            fh.write(_to_bytes("stop {ts}\n".format(ts=time.time())))
    except Exception:
        pass

    _say("command server starting (poll={p}ms, no runtime limit)".format(p=poll_ms))
    _say("work dir    : {r}".format(r=WORK_DIR))
    _say("command file: {c}".format(c=CMD_FILE))
    _say("result file : {r}".format(r=RESULT_FILE))
    _say("heartbeat   : {h}".format(h=HEARTBEAT_FILE))
    _say("stop file   : {s}".format(s=STOP_FILE))
    _say("legacy trigger accepted: {t}".format(t=LEGACY_TRIGGER))
    _say("executor    : {e} (re-executed per command)".format(e=EXEC_SCRIPT))

    counter = 0
    start = time.time()
    while True:
        _touch(HEARTBEAT_FILE)

        if os.path.isfile(STOP_FILE):
            _rm(STOP_FILE)
            _say("stop file seen - exiting")
            break

        handled = False

        # New protocol: atomic command file.
        if os.path.isfile(CMD_FILE):
            request = _load_request(CMD_FILE)
            if request is not None:
                _rm(CMD_FILE)
                counter += 1
                _dispatch(request, counter)
                handled = True

        # Legacy back-compat: old build.ps1 -Watch trigger = build command.
        if not handled and os.path.isfile(LEGACY_TRIGGER):
            _rm(LEGACY_TRIGGER)
            counter += 1
            _dispatch({"id": "trigger-{n}".format(n=counter), "cmd": "build",
                       "args": {}}, counter)

        system.delay(poll_ms)

    _say("server exit after {n} command(s), {t:.0f}s uptime".format(
        n=counter, t=time.time() - start))


main_server()
