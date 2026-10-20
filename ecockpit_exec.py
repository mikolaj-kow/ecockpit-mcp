# -*- coding: utf-8 -*-
# This comment is required if there are any non-ASCII characters in the script file.
"""
eCockpit command EXECUTOR - runs INSIDE e!COCKPIT's Python Scripting engine.

It is NOT a standalone script: it relies on globals injected by e!COCKPIT
(e_projects, e_system, system, Severity, ImportReporter, ConflictResolve,
online). It is normally driven by ecockpit_cmd_server.py, which exec()s this
file once per command and then calls run_command(request). It can also be
run directly (FILE -> Scripting -> Execute script file); a direct run
executes a BUILD.

Commands (request = {"id": ..., "cmd": ..., "args": {...}}):
    build      re-import XMLs + stale-POU cleanup + compile (default)
    import     re-import XMLs + cleanup only, no compile
    compile    compile only, no import (old --skip-import)
    status     project/application/device names, ip, simulated flag
    read       read one variable online.  args: {"path": "GVL.var"}
               (optional "device": name). Requires PLC credentials.
    download   connect, create boot application ON the PLC (replaces the
               running app!), start it, disconnect. args: {"confirm": true}
               and optional "device". Requires PLC credentials + confirm.
    disconnect log out of the PLC (optional "device").
    messages   dump the message-store category map (scripting API debugging)
    snapshot   save + copy the open project to <snapshots>/<project>_<v>.ecp
    probe      dump dir() of the scripting API objects to
               tmp/logs/ecockpit_api_probe.txt (API-surface discovery).

Configuration - ONE .env file next to this script (the ecockpit-mcp .env)
------------------------------------------------------------------------
ECP_PROJECT_ROOT   automation project repo (plcopensrc/ XML source tree,
                   .ecp discovery, default snapshots dir). NO default is
                   built in on purpose.
ECP_XML_DIR        XML source dir override (default: auto-discover
                   <project root>/plcopensrc or plcopensrc/<device>/)
ECP_SNAPSHOTS_DIR  snapshot destination (default: <project root>/snapshots)
ECP_PROJECTS_DIR   fallback search root for the open .ecp when it lives
                   outside the project repo (default: project root)
ECP_VERSION_POU    POU whose XML carries the version variable sAppVersion
                   (string constant initializer), used to name snapshots
ECP_WORK_DIR       file-protocol + logs work dir (default: tmp/ here)
ECP_PROJECT / ECP_LOG / ECP_BOOTAPP / ECP_SKIP_IMPORT / ECP_ENV_FILE
                   per-run overrides

PLC credentials (commands read/download only): ECP_PLC_USER / ECP_PLC_PASS
env vars win; otherwise the .env keys PLC_USER / PLC_PASS. There is NO
built-in default - without credentials the command fails with an
instruction. See .env.example.

Compiler-noise filter: .env key IGNORE_CODES (env override ECP_IGNORE_CODES),
comma/space separated (e.g. "C543, C100"), is dropped from the build/compile
harvest and counts; all_messages=true bypasses it for one run. Stale-POU
cleanup list: .env key REMOVED_POUS (env override ECP_REMOVED_POUS).

Error information on compile failure
------------------------------------
create_boot_application collapses every compiler diagnostic into a content-
free NullReferenceException. On failure this script harvests recent C0xxx
compiler messages from the framework log
    C:\\ProgramData\\WAGO Software\\e!COCKPIT\\logs\\e!COCKPIT.log
(full message texts land there, at least when a message is activated in the
Messages view - observed with C0087 on 2026-10-20) and embeds them, with
their original timestamps, into the build log. Empty harvest on an NRE means
"masked error".

The ImportReporter subclass is created in a factory so this module can be
imported by plain CPython for offline testing of the pure helpers.
"""

import os
import io
import sys
import glob
import json
import re
import traceback
import datetime
import xml.etree.ElementTree as ET


# --------------------------------------------------------------------------
# Configuration (single source: the .env next to this script)
# --------------------------------------------------------------------------
def _load_dotenv(path):
    """Minimal KEY=VALUE parser (no quotes stripping beyond whitespace).

    Lines: blank or starting with '#' are skipped; first '=' splits key and
    value; both sides are stripped. Values keep inner characters as-is.
    Returns {} when the file is missing. Runs on IronPython 2.7 and CPython 3.
    """
    out = {}
    try:
        with io.open(path, "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue
                if "=" not in line:
                    continue
                key, _, val = line.partition("=")
                key = key.strip()
                val = val.strip()
                if not key:
                    continue
                out[key] = val
    except Exception:
        pass
    return out


def _this_dir():
    """Directory of the running script.

    When exec()'d by ecockpit_cmd_server.py, __file__ is the SERVER's path
    (this file lives in the same directory), so the .env lookup below lands
    on the ecockpit-mcp .env in both direct-run and server-driven mode.
    """
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


ENV_FILE = os.environ.get("ECP_ENV_FILE", "") \
    or os.path.join(_this_dir(), ".env")
_ENV = _load_dotenv(ENV_FILE)


def _cfg(name, default=""):
    """ECP_* setting: environment variable wins over the .env file."""
    return os.environ.get(name, "") or _ENV.get(name, "") or default


# Automation project repo: plcopensrc/ XML sources, .ecp discovery, default
# snapshots dir. NO default is built in on purpose - this executor is
# project-agnostic.
PROJECT_ROOT   = _cfg("ECP_PROJECT_ROOT", "")

# File-protocol + logs work dir (shared with the other ecockpit-mcp parts).
WORK_DIR       = _cfg("ECP_WORK_DIR", "") or os.path.join(_this_dir(), "tmp")
LOG_DIR        = os.path.join(WORK_DIR, "logs")
DEFAULT_XML    = os.path.join(PROJECT_ROOT, "plcopensrc") if PROJECT_ROOT else ""
DEFAULT_LOG    = os.path.join(LOG_DIR, "ecockpit_build.log")
DEFAULT_BOOT   = os.path.join(WORK_DIR, "bootapp.app")
PROBE_OUT      = os.path.join(LOG_DIR, "ecockpit_api_probe.txt")
MSGS_DUMP_PATH = os.path.join(LOG_DIR, "ecockpit_messages_dump.txt")

SNAPSHOTS_DIR  = _cfg("ECP_SNAPSHOTS_DIR", "") \
    or (os.path.join(PROJECT_ROOT, "snapshots") if PROJECT_ROOT else "")
PROJECTS_DIR   = _cfg("ECP_PROJECTS_DIR", "") or PROJECT_ROOT
VERSION_POU    = _cfg("ECP_VERSION_POU", "")

# Framework log: the only observed disk channel carrying real compiler
# message texts (C0xxx). Read with shared access while e!COCKPIT holds it.
# Rotates at 10 MB - the .1 file holds recently rotated entries.
ECOCKPIT_LOG_DIR = r"C:\ProgramData\WAGO Software\e!COCKPIT\logs"
ECOCKPIT_LOG     = os.path.join(ECOCKPIT_LOG_DIR, "e!COCKPIT.log")

# Library Manager files hold CODESYS library bindings and must never be
# re-imported - importing them corrupts the project's library references.
SKIP_BASENAMES = {"Application.Library Manager.xml", "Library Manager.xml"}


def _removed_pous():
    """POUs/DUTs whose src XML was removed from the project repo.

    .env key REMOVED_POUS (comma/space separated; env override
    ECP_REMOVED_POUS). Import never deletes project objects, so stale copies
    keep compiling against removed symbols and break
    create_boot_application with an opaque NullReferenceException.
    Extend the list whenever a POU/DUT/GVL is removed from the project.
    """
    raw = os.environ.get("ECP_REMOVED_POUS", "") or _ENV.get("REMOVED_POUS", "")
    out = []
    for tok in raw.replace(";", ",").replace(" ", ",").split(","):
        tok = tok.strip()
        if tok:
            out.append(tok)
    return out


def _raw_script_args():
    """Return the raw argument string e!COCKPIT passes to the script.

    CLI form:   e!COCKPIT.exe --runscript=script.py --scriptargs:'a b c'
    The blob arrives in one of several places depending on the version, so
    probe them all.
    """
    candidates = []
    try:
        candidates.append(system.script_arguments)
    except Exception:
        pass
    try:
        candidates.append(script_arguments)  # may be a bare global
    except Exception:
        pass
    if isinstance(getattr(sys, "argv", None), list) and len(sys.argv) > 1:
        candidates.append(" ".join(sys.argv[1:]))
    for c in candidates:
        if isinstance(c, str) and c.strip():
            return c.strip()
        if isinstance(c, (list, tuple)) and c:
            return " ".join(str(x) for x in c)
    return ""


def parse_args():
    """Parse --flag value / --switch style args from --scriptargs."""
    import shlex
    out = {}
    raw = _raw_script_args()
    if raw:
        log("INFO", "scriptargs: {r}".format(r=raw))
    try:
        tokens = shlex.split(raw, posix=False)
    except Exception:
        tokens = raw.split()
    i = 0
    while i < len(tokens):
        t = tokens[i].strip().strip('"').strip("'")
        if t == "--skip-import":
            out["skip_import"] = True
            i += 1
            continue
        if t.startswith("--") and i + 1 < len(tokens):
            key = t[2:]
            val = tokens[i + 1].strip().strip('"').strip("'")
            out[key] = val
            i += 2
            continue
        i += 1
    return out


def discover_project_file():
    """Find a single .ecp under PROJECT_ROOT (one level deep). None if ambiguous."""
    if not os.path.isdir(PROJECT_ROOT):
        return ""
    hits = list(glob.glob(os.path.join(PROJECT_ROOT, "*.ecp"))) + \
           list(glob.glob(os.path.join(PROJECT_ROOT, "*", "*.ecp")))
    hits = [h for h in hits if not os.path.basename(h).lower().startswith("series_750")]
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        log("WARN", "multiple .ecp files found, please pass --project: "
                    + ", ".join(os.path.basename(h) for h in hits))
    return ""


def discover_xml_dir():
    """Locate the dir holding Application.*.xml under the project's
    plcopensrc/ - directly or one level deep (e.g. plcopensrc/<device>/).
    Empty string when not found."""
    if not PROJECT_ROOT:
        return ""
    base = os.path.join(PROJECT_ROOT, "plcopensrc")
    if not os.path.isdir(base):
        return ""
    if glob.glob(os.path.join(base, "Application.*.xml")):
        return base
    for d in sorted(glob.glob(os.path.join(base, "*"))):
        if os.path.isdir(d) and glob.glob(os.path.join(d, "Application.*.xml")):
            return d
    return ""


def resolve_config():
    args = parse_args()
    cfg = {
        "project":  args.get("project") or _cfg("ECP_PROJECT", "")
                    or discover_project_file(),
        "xml_dir":  args.get("xml-dir") or _cfg("ECP_XML_DIR", "")
                    or discover_xml_dir() or DEFAULT_XML,
        "log":      args.get("log")     or _cfg("ECP_LOG", DEFAULT_LOG),
        "bootapp":  args.get("bootapp") or _cfg("ECP_BOOTAPP", DEFAULT_BOOT),
        "skip_import": args.get("skip_import", False)
                    or _cfg("ECP_SKIP_IMPORT", "0") == "1",
    }
    if not cfg["project"]:
        log("WARN", "no --project given and no .ecp auto-discovered; "
            "will fall back to the already-open primary project")
    return cfg


# --------------------------------------------------------------------------
# Logging helpers
# --------------------------------------------------------------------------
_lines = []


def _ts():
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def log(level, msg):
    """Record a line in-memory and mirror it to the e!COCKPIT message view."""
    line = "[{ts}] [{lvl}] {msg}".format(ts=_ts(), lvl=level, msg=msg)
    _lines.append(line)
    # Also push to the e!COCKPIT Messages view so the user sees live progress.
    try:
        if level == "ERROR":
            system.write_message(Severity.Error, msg)
        elif level == "WARN":
            system.write_message(Severity.Warning, msg)
        else:
            system.write_message(Severity.Info, msg)
    except Exception:
        # write_message is best-effort; never let it mask the real error.
        pass
    print(line)


def _to_bytes(s):
    """Encode a str/unicode to UTF-8 bytes. Works on Python 2.7 and 3.x."""
    if isinstance(s, bytes):
        return s
    return s.encode("utf-8", "replace")


def _short_name(obj):
    """Reduce a verbose ScriptObject{...guid=...} repr to just 'Name=<n>'."""
    try:
        s = str(obj)
    except Exception:
        return "<?>"
    idx = s.find("Name=")
    if idx >= 0:
        tail = s[idx + len("Name="):]
        end = len(tail)
        for ch in (",", "}", ")"):
            pos = tail.find(ch)
            if pos >= 0 and pos < end:
                end = pos
        return "Name=" + tail[:end].strip()
    return s


def flush_log(request_id, cmd, summary):
    """Persist the collected lines plus a summary block to LOG_FILE."""
    for path in (LOG_FILE, BOOT_APP_FILE):
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)

    def _w(fh, text):
        fh.write(_to_bytes(text) + b"\n")

    with open(LOG_FILE, "wb") as fh:
        _w(fh, "eCockpit command log - {ts}".format(ts=_ts()))
        _w(fh, "command   : {c} (id {i})".format(c=cmd, i=request_id or "<direct>"))
        _w(fh, "project   : {p}".format(p=PROJECT_FILE or "<primary>"))
        _w(fh, "xml_dir   : {d}".format(d=XML_DIR))
        _w(fh, "skip_import: {s}".format(s=SKIP_IMPORT))
        _w(fh, "boot_app  : {b}".format(b=BOOT_APP_FILE))
        _w(fh, "-" * 78)
        for line in _lines:
            _w(fh, line)
        _w(fh, "-" * 78)
        _w(fh, "SUMMARY: {ok} | errors={e} warnings={w} imports={i} "
               "({iskip} skipped)".format(
                   ok="COMMAND OK" if summary["ok"] else "COMMAND FAILED",
                   e=summary["errors"],
                   w=summary["warnings"],
                   i=summary["imported"],
                   iskip=summary["skipped"]))
        # Make the success/failure bit trivially greppable for the agent.
        _w(fh, "RESULT=" + ("OK" if summary["ok"] else "FAILED"))


# Resolved once at import time so existing references work.
_CFG = resolve_config()
PROJECT_FILE  = _CFG["project"]
XML_DIR       = _CFG["xml_dir"]
SKIP_IMPORT   = _CFG["skip_import"]
LOG_FILE      = _CFG["log"]
BOOT_APP_FILE = _CFG["bootapp"]


# --------------------------------------------------------------------------
# PLC credentials (.env)
# --------------------------------------------------------------------------
def _require_creds():
    """Return (user, password) or raise with a fix-it instruction.

    Precedence: ECP_PLC_USER/ECP_PLC_PASS env > .env keys PLC_USER/PLC_PASS.
    No built-in default on purpose - a wrong default must never reach the PLC.
    """
    env = _load_dotenv(ENV_FILE)
    user = os.environ.get("ECP_PLC_USER", "") or env.get("PLC_USER", "")
    pw = os.environ.get("ECP_PLC_PASS", "") or env.get("PLC_PASS", "")
    if not user or not pw:
        raise RuntimeError(
            "PLC credentials missing for online command - add PLC_USER=<user> "
            "and PLC_PASS=<password> lines to {f} (template: .env.example "
            "next to the ecockpit-mcp scripts)".format(f=ENV_FILE))
    return user, pw


def _ignored_codes():
    """Compiler codes excluded from the harvest (known-noise warnings).

    .env key IGNORE_CODES (comma/space separated, e.g. "C543, C100"),
    env override ECP_IGNORE_CODES. Uppercased; matched against
    prefix+number ("C" + "543" = "C543"). Empty set = nothing ignored.
    """
    env = _load_dotenv(ENV_FILE)
    raw = os.environ.get("ECP_IGNORE_CODES", "") or env.get("IGNORE_CODES", "")
    codes = set()
    for tok in raw.replace(";", ",").replace(" ", ",").split(","):
        tok = tok.strip().upper()
        if tok:
            codes.add(tok)
    return codes


# --------------------------------------------------------------------------
# Framework-log helpers (shared read while e!COCKPIT holds the file open)
# --------------------------------------------------------------------------
def _read_file_shared(path):
    """Read a file's bytes even when another process holds it open (Windows).

    e!COCKPIT keeps its log files open with an exclusive lock for the whole
    session, so a plain open() fails with a sharing violation. This opens
    the file via Win32 CreateFileW requesting FILE_SHARE_READ|WRITE|DELETE.
    Returns bytes, or None on any failure. Guarded: runs on IronPython 2.7.
    """
    try:
        import ctypes
    except Exception:
        return None
    try:
        GENERIC_READ   = 0x80000000
        FILE_SHARE_ALL = 0x7        # READ | WRITE | DELETE
        OPEN_EXISTING  = 3
        INVALID_HANDLE = ctypes.c_void_p(-1).value

        kernel32 = ctypes.windll.kernel32
        CreateFileW = kernel32.CreateFileW
        CreateFileW.restype = ctypes.c_void_p
        handle = CreateFileW(path, GENERIC_READ, FILE_SHARE_ALL,
                             None, OPEN_EXISTING, 0, None)
        if not handle or handle == INVALID_HANDLE:
            return None
        try:
            ReadFile = kernel32.ReadFile
            CloseHandle = kernel32.CloseHandle
            chunks = []
            buf = ctypes.create_string_buffer(65536)
            got = ctypes.c_ulong(0)
            while True:
                ok = ReadFile(handle, buf, 65536, ctypes.byref(got), None)
                if not ok or got.value == 0:
                    break
                chunks.append(buf.raw[:got.value])
            return b"".join(chunks)
        finally:
            CloseHandle(handle)
    except Exception:
        return None


def _read_log_lines(path):
    """Read *path* into text lines, tolerant of e!COCKPIT's lock."""
    data = _read_file_shared(path)
    if data is None:
        try:
            with io.open(path, "rb") as fh:
                data = fh.read()
        except Exception:
            return None
    if data is None:
        return None
    try:
        text = data.decode("utf-8", "replace")
    except Exception:
        text = data.decode("latin-1", "replace")
    return text.splitlines()


_LOG_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
_C0_RE = re.compile(r"C0\d{3}")
_ACTIVATION_RE = re.compile(r"ActivateMessagesViewCommand' with parameter '(.*)' ->")


def _system_messages_text():
    """C0xxx-filtered dump of the Messages store via system.get_messages().

    The 2026-10-20 probe revealed this reader EXISTS (contrary to earlier
    assumption), but the no-arg form returns the script-trace flood, not
    compiler diagnostics - so this returns ONLY lines carrying C0xxx codes.
    Signature varies across builds; plausible forms are probed.
    Returns a list of lines, or None when unavailable/empty.
    """
    sysobj = globals().get("system")
    if sysobj is None:
        return None
    getter = getattr(sysobj, "get_messages", None)
    if getter is None:
        return None
    msgs = None
    used = None
    for form, args in (("()", ()), ("(None)", (None,))):
        try:
            msgs = getter(*args)
            used = form
            break
        except Exception:
            continue
    if msgs is None:
        return None
    log("INFO", "system.get_messages{f} succeeded".format(f=used))
    if isinstance(msgs, str):
        candidates = msgs.splitlines()
    else:
        try:
            candidates = [str(m) for m in msgs]
        except TypeError:
            candidates = str(msgs).splitlines()
    # Only lines that LOOK like real compiler messages ("C0087: ..." /
    # "W0042: ...") - our own write_message output also lands in the store
    # and prose mentioning "C0106-style" must not match.
    hits = []
    code_start_re = re.compile(r"^\s*[CW]\d{4}:")
    for ln in candidates:
        if code_start_re.search(ln):
            hits.append(ln.strip())
    return hits


def harvest_compiler_messages(lookback_hours=24, max_hits=40):
    """Collect recent C0xxx compiler messages from the framework log.

    Full compiler message texts reach e!COCKPIT.log at least via
    ActivateMessagesViewCommand lines (a message being activated in the
    Messages view, e.g. by a user click). Generic C0xxx-bearing lines are
    collected too, EXCEPT our own mirrored output (PythonScriptMessageManager
    lines echo this script's log() text, which mentions C0 codes itself).

    Returns a list of "(timestamp) text" strings, newest last, deduped.
    Timestamps are the ORIGINAL entry times so stale entries stay
    identifiable - an entry predating this build may still describe exactly
    the masked failure (it was click-carried into the log after the fact).

    Scans the live framework log AND e!COCKPIT.log.1: the log rotates at
    10 MB, so recent entries (e.g. a message clicked this morning) can sit
    in the .1 file.
    """
    lines = []
    for name in ("e!COCKPIT.log", "e!COCKPIT.log.1"):
        part = _read_log_lines(os.path.join(ECOCKPIT_LOG_DIR, name))
        if part:
            lines.extend(part)
    if not lines:
        return []
    cutoff = datetime.datetime.now() - datetime.timedelta(hours=lookback_hours)
    hits = []
    seen = set()
    for ln in lines:
        if "PythonScriptMessageManager" in ln:
            continue  # our own mirrored log text, not compiler output
        ts = None
        m = _LOG_TS_RE.match(ln)
        if m:
            try:
                ts = datetime.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            except Exception:
                ts = None
            if ts is not None and ts < cutoff:
                continue
        elif not ln.strip():
            continue
        text = None
        am = _ACTIVATION_RE.search(ln)
        if am is not None:
            candidate = am.group(1)
            if _C0_RE.search(candidate):
                text = candidate
        elif _C0_RE.search(ln):
            # Keep the tail of the line; the prefix is framework logger noise.
            text = ln.strip()
            if len(text) > 300:
                text = text[:300]
        if text is None or text in seen:
            continue
        seen.add(text)
        hits.append("({ts}) {t}".format(
            ts=m.group(1) if m else "no-ts", t=text))
        if len(hits) >= max_hits:
            break
    return hits


def _iter_message_texts(max_per_cat=3000):
    """Walk EVERY category's message OBJECTS; return per-category texts.

    Per-category get_message_objects(cat) is the only enumeration that
    reaches build/compile messages (2026-10-20): the no-arg form returns
    script messages only, and the get_messages(cat) TEXT getter returns 0
    for all categories. Objects expose .text. Categories are source-
    separated (build category vs script category), which is what makes
    clean filtering possible.
    Returns [(cat_guid, [text, ...]), ...] for non-empty categories.
    """
    sysobj = globals().get("system")
    per_cat = []
    if sysobj is None:
        return per_cat
    go = getattr(sysobj, "get_message_objects", None)
    gcat = getattr(sysobj, "get_message_categories", None)
    if go is None or gcat is None:
        return per_cat
    cats = None
    try:
        cats = gcat()
        cat_names = [str(c) for c in cats]
    except Exception:
        try:
            cat_names = [str(cats)]
        except Exception:
            cat_names = []
    for cat in cat_names:
        try:
            objs = go(cat)
        except Exception:
            continue
        texts = []
        try:
            for o in objs:
                if len(texts) >= max_per_cat:
                    break
                text = None
                for a in ("text", "Text", "message", "Message"):
                    try:
                        v = getattr(o, a)
                        if v is not None:
                            text = str(v)
                            break
                    except Exception:
                        continue
                texts.append(text if text is not None else str(o))
        except Exception:
            continue
        if texts:
            per_cat.append((cat, texts))
    return per_cat


def _flush_message_queue():
    """Pump the UI/message loop so queued messages reach the store.

    The script runs on the UI thread; messages the compiler posted during a
    failed create_boot_application may sit unflushed. Best-effort.
    """
    sysobj = globals().get("system")
    if sysobj is None:
        return
    pml = getattr(sysobj, "process_messageloop", None)
    if pml is None:
        return
    for _ in range(3):
        try:
            pml()
        except Exception:
            return
        try:
            system.delay(100)
        except Exception:
            pass


def _our_script_line(ln):
    """True when a message text was posted by OUR own script machinery."""
    if ln.startswith("[server]") or ln.startswith("[watch]"):
        return True
    if re.match(r"^\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\]", ln):
        return True  # our log() mirror lines
    if ".py(" in ln or "Begin of script" in ln:
        return True  # script tracer output
    if "DeprecationWarning" in ln or "A parser for XML" in ln:
        return True  # IronPython startup junk
    if 'File "<string>"' in ln or "Traceback (most recent call last):" in ln:
        return True  # our crash echoes
    if "SystemError:" in ln or "create_boot_application raised" in ln:
        return True
    if "NullReferenceException carries" in ln or "no --project given" in ln:
        return True
    if " RESULT=" in ln or ln.startswith("RESULT="):
        return True
    if ln.startswith("traceback:") or ln.startswith("compiling (create_boot"):
        return True
    return False


def append_harvest_section(had_compile_error):
    """Append readable compiler messages to the in-memory log.

    Channel 1 (preferred): the message OBJECT store after a
    process_messageloop flush. Compile diagnostics appear there as plain
    text WITHOUT C-code prefixes (proven 2026-10-20 with a GUI build:
    "Compile complete -- 21 errors, 10 warnings" + all error texts). They
    belong to the LAST build that emitted diagnostics - possibly a GUI
    build, so freshness must be judged against this build's outcome.
    Channel 2 (fallback): recent C0xxx lines from the framework
    e!COCKPIT.log (full texts land there when a message is activated in
    the Messages view, e.g. by a double-click).
    """
    _flush_message_queue()
    per_cat = []
    try:
        per_cat = _iter_message_texts()
    except Exception as exc:
        log("WARN", "message-object walk raised: {e}".format(e=exc))
    # Category-level selection: a category counts as compiler output when it
    # contains any known compile narrative/diagnostic pattern. This drops the
    # script-message categories wholesale (our raw write_message texts carry
    # no prefix, so line-level filtering alone cannot separate them).
    _cat_hints = ("Build started", "Typify", "Generate code",
                  "Compile complete", "Additional code check",
                  "is no component of", "not defined", "requires exactly",
                  "has not been added", "reserved keyword",
                  "is already used in another task")
    code_re = re.compile(r"^\s*[CW]\d{4}:")
    from_store = []
    seen = set()
    cats_used = 0
    for cat, texts in per_cat:
        if not any(h in t for t in texts for h in _cat_hints) and \
                not any(code_re.search(t) for t in texts):
            continue
        cats_used += 1
        for ln in texts:
            stripped = ln.strip()
            if not stripped or stripped in seen or _our_script_line(stripped):
                continue
            seen.add(stripped)
            from_store.append(stripped)
    # Summary line first for at-a-glance reading.
    from_store.sort(key=lambda s: (0 if ("Compile complete" in s or
                                         "Build complete" in s or
                                         "Build started" in s) else 1, s))
    hits = []
    try:
        hits = harvest_compiler_messages()
    except Exception as exc:
        log("WARN", "framework-log harvest raised: {e}".format(e=exc))
    _lines.append("")
    _lines.append("----- Compiler message harvest -----")
    got = False
    if from_store:
        got = True
        _lines.append("[message object store, {c} compile-related categories "
                      "- belongs to the LAST build that emitted diagnostics, "
                      "possibly a GUI build; judge freshness yourself]".format(
                          c=cats_used))
        for ln in from_store[:100]:
            _lines.append(ln)
    if hits:
        got = True
        _lines.append("[framework e!COCKPIT.log, last 24 h, original timestamps]")
        for h in hits:
            _lines.append(h)
    if not got:
        _lines.append("(no compile-related categories in the object store "
                      "({c} categories walked) and no C0xxx entries in the "
                      "framework log)".format(c=len(per_cat)))
    if had_compile_error and not got:
        _lines.append("NOTE: the compile failure above raised only a generic "
                      "exception and no diagnostics were readable. This is a "
                      "MASKED error. Fallback: run a build once from the "
                      "e!COCKPIT GUI (Build menu) - GUI builds emit the full "
                      "diagnostics into the message store, which the next "
                      "harvest reads.")
    _lines.append("")


def cmd_messages():
    """Map the scriptable Messages store: categories, descriptions, sizes.

    Probed live 2026-10-20: 5 GUID categories, plain get_messages() returned
    the script-trace flood, no C0xxx anywhere. This version also resolves
    each GUID via get_message_category_description and probes the structure
    of get_message_objects per category, so we know exactly what is
    readable. Run before/after a build to see whether compile failures add
    anything to the store.
    """
    sysobj = globals().get("system")
    if sysobj is None:
        return "unavailable"
    gm = getattr(sysobj, "get_messages", None)
    if gm is None:
        return "unavailable"

    def _call_best_effort(func, arg=None):
        """Try func(arg) then func(); return the first success or None."""
        for args in ((arg,), ()):
            try:
                return func(*args)
            except Exception:
                continue
        return None

    def _to_text(res):
        if res is None:
            return ""
        if isinstance(res, str):
            return res
        try:
            return "\n".join(str(x) for x in res)
        except TypeError:
            return str(res)

    # 1. Categories + human descriptions.
    cats = _call_best_effort(getattr(sysobj, "get_message_categories", None))
    cat_names = []
    if cats is not None:
        try:
            cat_names = [str(c) for c in cats]
        except TypeError:
            cat_names = [str(cats)]
    gdesc = getattr(sysobj, "get_message_category_description", None)
    entries = []
    for cat in cat_names:
        desc = "<no description getter>"
        if gdesc is not None:
            got = _call_best_effort(gdesc, cat)
            desc = "<failed>" if got is None else str(got)
        entries.append({"guid": cat, "description": desc})

    # 2. Per-category size + C0xxx content via get_messages(cat).
    c0_re = re.compile(r"C0\d{3}")
    c0_hits = []
    for entry in entries:
        text = _call_best_effort(gm, entry["guid"]) if gm else None
        lines = _to_text(text).splitlines() if text is not None else []
        hits = [ln.strip() for ln in lines if c0_re.search(ln)]
        entry["lines"] = len(lines)
        entry["c0xxx"] = len(hits)
        for h in hits:
            if h not in c0_hits:
                c0_hits.append(h)

    # 3. THE KEY PROBE: per-category message OBJECTS. The compile
    #    diagnostics live here as PLAIN TEXT WITHOUT C-CODE PREFIXES
    #    (proven 2026-10-20 14:49 after a GUI build: category with
    #    "Compile complete -- 21 errors, 10 warnings" + all error texts).
    c0_re = re.compile(r"[CW]\d{4}:")
    go = getattr(sysobj, "get_message_objects", None)
    objects_summary = {"total": 0, "c0xxx": [], "build_lines": []}
    dump = []
    wide_attrs = ["code", "Code", "pou", "Pou", "pouName", "PouName",
                  "object", "Object", "line", "Line", "column", "Column",
                  "position", "Position", "description", "Description",
                  "severity", "Severity", "time", "Time", "timestamp",
                  "TimeStamp", "instance", "Instance", "project", "Project"]
    attr_probe_done = [False]
    if go is not None:
        for entry in entries:
            cat = entry["guid"]
            try:
                objs = go(cat)
            except Exception:
                continue
            count = 0
            c0_in_cat = 0
            try:
                for o in objs:
                    count += 1
                    if count > 3000:
                        break
                    text = None
                    for a in ("text", "Text", "message", "Message"):
                        try:
                            v = getattr(o, a)
                            if v is not None:
                                text = str(v)
                                break
                        except Exception:
                            continue
                    if text is None:
                        text = str(o)
                    dump.append("[{c}] {t}".format(c=cat[:8], t=text))
                    stripped = text.strip()
                    if stripped.startswith("------ Build") and \
                            len(objects_summary["build_lines"]) < 3:
                        objects_summary["build_lines"].append(stripped[:120])
                    # One-time wide attribute probe on a diagnostics-looking
                    # object (POU/line/code may ride as separate attributes).
                    if not attr_probe_done[0] and (
                            "is no component of" in stripped
                            or "not defined" in stripped
                            or "requires exactly" in stripped
                            or "Compile complete" in stripped):
                        attr_probe_done[0] = True
                        found = {}
                        for a in wide_attrs:
                            try:
                                v = getattr(o, a)
                                if v is not None and str(v) != text:
                                    found[a] = str(v)[:80]
                            except Exception:
                                continue
                        if found:
                            objects_summary["attr_probe"] = found
                            log("INFO", "message object extra attrs: {f}".format(
                                f=json.dumps(found)))
                    m = c0_re.search(stripped)
                    if m and "trust harness" not in stripped:
                        c0_in_cat += 1
                        if len(objects_summary["c0xxx"]) < 40:
                            objects_summary["c0xxx"].append(
                                "[{c}] {t}".format(c=cat[:8], t=stripped[:250]))
            except Exception as exc:
                entry["objects_error"] = str(exc)[:80]
                continue
            objects_summary["total"] += count
            entry["objects"] = count
            entry["c0xxx_objects"] = c0_in_cat
    try:
        d = os.path.dirname(MSGS_DUMP_PATH)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        with open(MSGS_DUMP_PATH, "wb") as fh:
            for ln in dump:
                fh.write(_to_bytes(ln + "\n"))
        log("INFO", "full dump ({n} texts) -> {p}".format(n=len(dump), p=MSGS_DUMP_PATH))
    except Exception:
        pass
    log("INFO", "message objects total (per-category walk): {n}".format(
        n=objects_summary["total"]))
    log("INFO", "build-related samples: {b}".format(
        b=objects_summary["build_lines"]))
    for h in objects_summary["c0xxx"]:
        log("INFO", "OBJECT C0xxx: {h}".format(h=h))
    return {
        "categories": entries,
        "objects": objects_summary,
        "ignore_codes": sorted(_ignored_codes()),
        "ignore_note": ("build/compile harvest filters these codes "
                        "(IGNORE_CODES in .env); pass all_messages=true "
                        "to bypass for one run"),
    }


# --------------------------------------------------------------------------
# Project access
# --------------------------------------------------------------------------
def get_project():
    """Open PROJECT_FILE if given, else reuse the already-open primary project."""
    if PROJECT_FILE:
        log("INFO", "opening project {p}".format(p=PROJECT_FILE))
        return e_projects.open_project(PROJECT_FILE)
    primary = getattr(e_projects, "primary", None)
    if primary is None:
        raise RuntimeError("No project open and no --project given. "
                           "Open the .ecp in e!COCKPIT or pass --project.")
    log("INFO", "using already-open primary project")
    return primary


def get_application(project):
    apps = project.find("Application", True)
    if apps is None or apps.Count == 0:
        raise RuntimeError("Project has no 'Application' node.")
    app = apps[0]
    log("INFO", "target application: {a}".format(a=_short_name(app)))
    return app


def get_device(project, name=""):
    """Return a device object: by name when given, else the first one."""
    if name:
        log("INFO", "device lookup by name: {n}".format(n=name))
        return project.get_device(name)
    devices = project.devices
    count = getattr(devices, "Count", 0)
    if not count:
        raise RuntimeError("Project has no devices configured.")
    dev = devices[0]
    log("INFO", "using first project device: {d}".format(d=_short_name(dev)))
    return dev


# --------------------------------------------------------------------------
# ImportReporter (factory: keeps the module importable without e!COCKPIT)
# --------------------------------------------------------------------------
def make_import_reporter(xml_path, summary):
    """Build an ImportReporter that funnels callbacks into our logfile.

    Factory instead of a module-level class so plain CPython can import
    this module for offline testing (ImportReporter only exists inside
    e!COCKPIT). resolve_conflict returns Replace when available so
    re-importing an already-present POU/DUT/GVL overwrites it.
    """

    class AgentImportReporter(ImportReporter):
        def __init__(self):
            self.xml_path = xml_path
            self.file_errors = 0
            self.file_warnings = 0

        def error(self, message):
            self.file_errors += 1
            summary["errors"] += 1
            log("ERROR", "import {f}: {m}".format(
                f=os.path.basename(xml_path), m=message))

        def warning(self, message):
            self.file_warnings += 1
            summary["warnings"] += 1
            log("WARN", "import {f}: {m}".format(
                f=os.path.basename(xml_path), m=message))

        def resolve_conflict(self, obj):
            try:
                return ConflictResolve.Replace
            except (AttributeError, NameError):
                return ConflictResolve.Copy

        def added(self, obj):
            log("INFO", "import {f}: added {o}".format(
                f=os.path.basename(xml_path), o=_short_name(obj)))

        def replaced(self, obj):
            log("INFO", "import {f}: replaced {o}".format(
                f=os.path.basename(xml_path), o=_short_name(obj)))

        def skipped(self, obj):
            log("INFO", "import {f}: skipped {o}".format(
                f=os.path.basename(xml_path), o=_short_name(obj)))

        @property
        def aborting(self):
            return False

    return AgentImportReporter()


# --------------------------------------------------------------------------
# Import + cleanup
# --------------------------------------------------------------------------
def import_xml_files(app, summary):
    """Re-import every Application.*.xml (except Library Manager) into app."""
    if not os.path.isdir(XML_DIR):
        raise RuntimeError(
            "XML dir not found: {d!r} - set ECP_PROJECT_ROOT (or ECP_XML_DIR) "
            "in the ecockpit-mcp .env to your automation project".format(
                d=XML_DIR))

    xml_files = sorted(
        f for f in glob.glob(os.path.join(XML_DIR, "Application.*.xml"))
        if os.path.basename(f) not in SKIP_BASENAMES
    )
    if not xml_files:
        raise RuntimeError("No Application.*.xml files found in {d}".format(d=XML_DIR))

    log("INFO", "importing {n} XML files from {d}".format(n=len(xml_files), d=XML_DIR))

    for path in xml_files:
        base = os.path.basename(path)
        reporter = make_import_reporter(path, summary)
        try:
            # Preferred signature: import_xml(reporter, path)
            app.import_xml(reporter, path)
            summary["imported"] += 1
            if reporter.file_errors:
                summary["skipped"] += 1
                log("ERROR", "{f}: imported with {e} error(s)".format(
                    f=base, e=reporter.file_errors))
            else:
                log("OK", "{f}: imported".format(f=base))
        except TypeError:
            # Older e!COCKPIT versions use import_xml(path, no_ui_hints_bool).
            try:
                app.import_xml(path, False)
                summary["imported"] += 1
                log("OK", "{f}: imported (legacy signature)".format(f=base))
            except Exception:
                summary["errors"] += 1
                summary["skipped"] += 1
                log("ERROR", "{f}: import failed\n{tb}".format(
                    f=base, tb=traceback.format_exc()))
        except Exception:
            summary["errors"] += 1
            summary["skipped"] += 1
            log("ERROR", "{f}: import failed\n{tb}".format(
                f=base, tb=traceback.format_exc()))


def cleanup_removed_objects(project, summary):
    """Delete stale project objects whose src XML left the repo (REMOVED_POUS)."""
    for name in _removed_pous():
        try:
            hits = project.find(name, True)
        except Exception:
            log("WARN", "cleanup: find('{n}') failed - delete it manually if present".format(n=name))
            continue
        if not hits or getattr(hits, "Count", 0) == 0:
            continue
        for h in hits:
            deleted = False
            for method_name in ("delete", "remove", "destroy"):
                m = getattr(h, method_name, None)
                if m is None:
                    continue
                try:
                    m()
                    deleted = True
                    log("OK", "cleanup: deleted stale project object '{n}' via .{m}()".format(
                        n=name, m=method_name))
                    break
                except Exception:
                    continue
            if not deleted:
                log("WARN", "cleanup: could not delete '{n}' via script API - "
                            "delete it manually in the eCockpit project tree".format(n=name))


# --------------------------------------------------------------------------
# Compile (+ harvest)
# --------------------------------------------------------------------------
def _read_structured_messages():
    """Read compile diagnostics via the documented IScriptMessage API.

    ScriptEngine.chm (IScriptApplication.build/generate_code remarks):
    after a build, check get_messages(Guid) / get_message_objects(Guid,
    Severity). IScriptMessage carries prefix ("compile") + number (the
    C-code), text, severity, object (the POU) and position_text (line) -
    structured diagnostics, no text scraping. Script/system messages have
    no prefix and are skipped.

    NOTE: attribute access uses try/except chains around DIRECT syntax
    (obj.text) - the getattr() builtin fails on these .NET interop objects
    even for members that work via direct access (observed 2026-10-20).
    """
    out = []
    try:
        cats = system.get_message_categories()
    except Exception as exc:
        log("WARN", "get_message_categories failed: {e}".format(e=exc))
        return out
    for cat in cats:
        try:
            msgs = system.get_message_objects(cat)
        except Exception:
            continue
        for m in msgs:
            try:
                prefix = m.prefix
            except Exception:
                prefix = None
            if not prefix:
                continue  # script/system noise has no prefix
            entry = {"category": str(cat)[:8], "prefix": str(prefix)}
            # Direct-syntax access per documented member.
            for attr in ("number", "text", "severity", "position_text"):
                try:
                    if attr == "number":
                        entry[attr] = str(m.number)
                    elif attr == "text":
                        entry[attr] = str(m.text)
                    elif attr == "severity":
                        entry[attr] = str(m.severity)
                    elif attr == "position_text":
                        entry[attr] = str(m.position_text)
                except Exception:
                    pass
            try:
                obj = m.object
                if obj is not None:
                    entry["object"] = _short_name(obj)
            except Exception:
                pass
            out.append(entry)
    return out


def _format_structured_messages(entries, ignored_codes=None):
    """Format structured messages; returns (lines, errors, warnings,
    ignored_counts).

    Entries whose prefix+number is in ignored_codes are dropped from the
    output AND the counts (known-noise filter, IGNORE_CODES in .env);
    ignored_counts maps each ignored code to how many entries were dropped.
    """
    ignored_codes = ignored_codes or set()
    lines = []
    errors = 0
    warnings = 0
    ignored_counts = {}
    for e in entries:
        code = ""
        if e.get("number") and e.get("number") != "None":
            code = "{p}{n}".format(
                p=e.get("prefix", ""), n=e.get("number")).upper()
        if code and code in ignored_codes:
            ignored_counts[code] = ignored_counts.get(code, 0) + 1
            continue
        prefix = e.get("prefix", "")
        if code:
            code = code + ": "
        sev = e.get("severity", "")
        if "Error" in sev or "Fatal" in sev:
            errors += 1
        elif "Warning" in sev:
            warnings += 1
        loc = e.get("object", "")
        pos = e.get("position_text", "")
        where = " ".join(x for x in (loc, pos) if x and x != "None")
        line = "{code}{sev}: {text}{where}".format(
            code=code, sev=sev or "?",
            text=e.get("text", "")[:200],
            where=" [{w}]".format(w=where) if where else "")
        lines.append(line)
    return lines, errors, warnings, ignored_counts


def compile_project(app, project, summary, args=None):
    """Compile the application via the DOCUMENTED script API.

    Primary: IScriptApplication.build() (ScriptEngine.chm) - builds the
    application and posts the full compile narrative + structured
    IScriptMessage diagnostics (prefix/number/text/severity/object/
    position_text) into the message store, which we read right after.
    Fallback: create_boot_application(path) - legacy vehicle whose final
    step dies with a content-free NullReferenceException on compile errors.

    args.all_messages=true bypasses the IGNORE_CODES known-noise filter
    (.env) for this run; otherwise ignored codes drop out of the harvest
    and the counts, replaced by a one-line summary.
    """
    log("INFO", "compiling (app.build() - documented IScriptApplication API)")
    build_raised = None
    build_available = True
    try:
        app.build()
        log("OK", "app.build() returned without exception")
    except AttributeError:
        build_available = False
        log("WARN", "app.build() not available on this object - falling "
                    "back to create_boot_application")
    except Exception as exc:
        build_raised = exc
        log("ERROR", "app.build() raised: {e}".format(e=exc))
        log("ERROR", "traceback:\n{tb}".format(tb=traceback.format_exc()))

    # Structured diagnostics from the message store (always read fresh).
    entries = _read_structured_messages()
    bypass = bool((args or {}).get("all_messages"))
    ignored = set() if bypass else _ignored_codes()
    lines, s_errors, s_warnings, ignored_counts = _format_structured_messages(
        entries, ignored)
    if entries:
        summary["errors"] += s_errors
        summary["warnings"] += s_warnings
        harvest = lines[:60]
        if ignored_counts:
            detail = ", ".join("{c} x{n}".format(c=c, n=n)
                               for c, n in sorted(ignored_counts.items()))
            harvest.append("(+{n} ignored by IGNORE_CODES: {d})".format(
                n=sum(ignored_counts.values()), d=detail))
        summary["harvest"] = harvest
        summary["harvest_ignored"] = ignored_counts
        _lines.append("")
        if bypass:
            _lines.append("(IGNORE_CODES bypassed for this run - "
                          "all_messages=true)")
        _lines.append("----- Compiler messages (IScriptMessage, "
                      "structured) -----")
        for ln in lines[:100]:
            _lines.append(ln)
        if len(lines) > 100:
            _lines.append("... {n} more".format(n=len(lines) - 100))
        _lines.append("")

    if build_available:
        if build_raised is None and s_errors == 0:
            summary["ok"] = True
            log("OK", "compile OK (app.build(), {e} errors / {w} warnings "
                      "in message store)".format(e=s_errors, w=s_warnings))
        else:
            summary["ok"] = False
            if build_raised is None:
                summary["errors"] += 1 if s_errors == 0 else 0
            log("ERROR", "compile FAILED (app.build(); store counts: "
                         "{e} errors / {w} warnings)".format(
                             e=s_errors, w=s_warnings))
        # Boot application file: only meaningful on a clean compile.
        if summary["ok"]:
            try:
                app.create_boot_application(BOOT_APP_FILE)
                log("OK", "boot application written to {b}".format(b=BOOT_APP_FILE))
            except Exception as exc:
                log("WARN", "create_boot_application failed after clean "
                            "build: {e}".format(e=exc))
        append_harvest_section(build_raised is not None or s_errors > 0)
        return

    # Legacy fallback: create_boot_application (also writes the boot file).
    log("INFO", "compiling (create_boot_application -> {b})".format(b=BOOT_APP_FILE))
    had_error = False
    try:
        app.create_boot_application(BOOT_APP_FILE)
        summary["ok"] = True
        log("OK", "compile succeeded, boot application written to {b}".format(b=BOOT_APP_FILE))
        return
    except Exception as exc:
        had_error = True
        summary["ok"] = False
        summary["errors"] += 1
        msg = str(exc)
        log("ERROR", "create_boot_application raised: {e}".format(e=exc))
        log("ERROR", "traceback:\n{tb}".format(tb=traceback.format_exc()))
        if "Object reference" in msg or "NullReference" in msg \
                or "instance of an object" in msg:
            log("ERROR", "This NullReferenceException carries no diagnostics "
                         "by itself - see the harvest section below for the "
                         "real compiler messages (C0xxx), if any were "
                         "captured. A C0106-style 'out of global data "
                         "memory' behind this mask needs a one-time "
                         "Build -> Clean in e!COCKPIT.")
    if had_error:
        append_harvest_section(had_error)


# --------------------------------------------------------------------------
# Online commands (read / download / disconnect / status / probe)
# --------------------------------------------------------------------------
def _online_connect(project, args):
    """Connect to the PLC (login) and return (device, onlineapp).

    Requires PLC_USER/PLC_PASS (.env or env vars). Caller disconnects.
    """
    user, pw = _require_creds()
    device = get_device(project, args.get("device", ""))
    try:
        aa = project.active_application
        app = aa if aa is not None else get_application(project)
    except Exception:
        app = get_application(project)
    log("INFO", "connecting to PLC as user '{u}' (login)".format(
        u=user[0] + "***" if len(user) > 1 else "***"))
    online.set_default_credentials(user, pw)
    device.connect()
    log("OK", "connected (logged in)")
    onlineapp = online.create_online_application(app)
    return device, onlineapp


def _online_disconnect(device):
    try:
        device.disconnect()
        log("INFO", "disconnected from PLC")
    except Exception as exc:
        log("WARN", "disconnect failed: {e}".format(e=exc))


def _display_name(obj):
    """Friendlier name for dynamic ScriptObjects: probe get_name()/.name/.Name.

    dir() on these wrappers is empty (probe 2026-10-20), so attributes must
    be probed directly; fall back to the repr short-name.
    """
    try:
        gn = getattr(obj, "get_name", None)
        if gn is not None:
            v = gn()
            if v:
                return str(v)
    except Exception:
        pass
    for attr in ("name", "Name"):
        try:
            v = getattr(obj, attr)
            if v:
                return str(v)
        except Exception:
            continue
    return _short_name(obj)


def cmd_status(project):
    """Collect project/application/device info as a plain dict."""
    info = {"project": _display_name(project)}
    try:
        aa = project.active_application
        info["active_application"] = _display_name(aa) if aa is not None else None
    except Exception:
        info["active_application"] = None
    devs = []
    try:
        for d in project.devices:
            entry = {"name": _display_name(d)}
            for attr in ("ip_address", "is_simulated", "device_guid"):
                try:
                    entry[attr] = str(getattr(d, attr))
                except Exception:
                    pass
            devs.append(entry)
    except Exception as exc:
        devs.append({"error": str(exc)})
    info["devices"] = devs
    log("OK", "status: {j}".format(j=json.dumps(info)))
    return info


def cmd_read(project, args):
    """Read one variable online and return its string value."""
    path = args.get("path", "")
    if not path:
        raise RuntimeError("read needs args.path, e.g. "
                           "{\"path\": \"GVLName.Variable\"}")
    device, onlineapp = _online_connect(project, args)
    try:
        value = onlineapp.read_value(path)
        log("OK", "read {p} = {v}".format(p=path, v=value))
        return str(value)
    finally:
        _online_disconnect(device)


def cmd_download(project, args):
    """Login, create the boot application ON the PLC (download), start it."""
    if not args.get("confirm"):
        raise RuntimeError("download REFUSED - requires args.confirm=true "
                           "(CLI: -Download -Confirm). It replaces the "
                           "running application on the PLC.")
    device, onlineapp = _online_connect(project, args)
    try:
        log("INFO", "creating boot application ON DEVICE (this downloads the "
                    "application and replaces the running one)")
        onlineapp.create_boot_application()
        log("INFO", "starting application on device")
        device.start()
        log("OK", "download complete, application started")
    finally:
        _online_disconnect(device)


def cmd_disconnect(project, args):
    """Explicit logout from the PLC."""
    device = get_device(project, args.get("device", ""))
    _online_disconnect(device)


_PROBE_KEYWORDS = ("compile", "check", "generate", "message", "online",
                   "login", "logout", "value", "boot", "read", "write",
                   "connect", "device")


def _detect_app_version():
    """Read the version variable (sAppVersion) from the configured version
    POU's source XML (ECP_VERSION_POU in .env). Empty when unset/not found."""
    if not VERSION_POU:
        return ""
    path = os.path.join(XML_DIR,
                        "Application.{p}.xml".format(p=VERSION_POU))
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "rb") as fh:
            text = fh.read().decode("utf-8", "replace")
    except Exception:
        return ""
    m = re.search(r'name="sAppVersion".{0,400}?value="\'([^\']+)\'"', text, re.S)
    if m:
        return m.group(1)
    return ""


def _find_open_source_ecp():
    """Newest *.ecp under the projects search root (the GUI keeps a .~u lock
    beside it).

    Fallback for projects that live OUTSIDE the automation repo
    (ECP_PROJECTS_DIR, defaults to ECP_PROJECT_ROOT) - repo auto-discovery
    cannot find those. Skips the snapshots dir itself and editor lock files.
    """
    hits = []
    for root, dirs, files in os.walk(PROJECTS_DIR):
        dirs[:] = [d for d in dirs if d.lower() != "snapshots"]
        for f in files:
            if f.lower().endswith(".ecp") and not f.lower().endswith(".ecp.~u"):
                hits.append(os.path.join(root, f))
    if not hits:
        return ""
    return max(hits, key=lambda p: os.path.getmtime(p))


def _version_from_filename(path):
    """Extract a dotted version number from the filename (MyApp_1.2.3.ecp,
    myapp-v3.9.6.ecp -> 1.2.3 / 3.9.6)."""
    base = os.path.basename(path)
    m = re.search(r"(\d+(?:\.\d+)+)", base)
    return m.group(1) if m else ""


def _snapshot_prefix(project):
    """Filename-safe project name for snapshot files (fallback: 'project')."""
    try:
        name = _display_name(project)
    except Exception:
        name = ""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", name or "").strip("_")
    return safe or "project"


def cmd_snapshot(project, args):
    """Snapshot the project to SNAPSHOTS_DIR as <project>_<version>.ecp.

    Flow: project.save() first (persist current in-memory state to the
    on-disk .ecp) - NOTE: this fails when the GUI session holds the project
    under a temporary file-name ("Current project is stored with a
    temporary file-name"), in which case the LAST on-disk state is snapped
    and the warning is logged. project.save_as() is deliberately NOT used:
    it would RETARGET the open project to the snapshot file. The snapshot
    itself is a binary file copy.
    Version priority: args.version > version variable in the ECP_VERSION_POU
    source XML > dotted number in the source filename. Overwrites an
    existing snapshot of the same version by design.
    """
    save_ok = True
    try:
        project.save()
        log("OK", "project saved in place (current state persisted to the .ecp)")
    except Exception as exc:
        save_ok = False
        log("WARN", "project.save() failed ({e}) - snapshotting the last "
                    "on-disk state instead".format(e=exc))
    src = ""
    try:
        # IScriptProject.path - the open project's on-disk location
        # (ScriptEngine.chm, referenced by the save_as remarks).
        p = project.path
        if p and os.path.isfile(str(p)):
            src = str(p)
            log("INFO", "project.path: {p}".format(p=src))
    except Exception:
        pass
    if not src:
        src = PROJECT_FILE or discover_project_file()
    if not src or not os.path.isfile(src):
        src = _find_open_source_ecp()
    if not src or not os.path.isfile(src):
        raise RuntimeError("cannot locate the on-disk .ecp to snapshot "
                           "(repo discovery and {d} search both empty)".format(
                               d=PROJECTS_DIR))
    version = args.get("version") or _detect_app_version() \
        or _version_from_filename(src)
    if not version:
        raise RuntimeError("could not detect a version (ECP_VERSION_POU "
                           "variable and filename both failed) - pass "
                           'args.version e.g. {"version": "3.10.0"}')
    if not SNAPSHOTS_DIR:
        raise RuntimeError("no snapshot destination configured - set "
                           "ECP_PROJECT_ROOT or ECP_SNAPSHOTS_DIR in the "
                           "ecockpit-mcp .env")
    if not os.path.isdir(SNAPSHOTS_DIR):
        os.makedirs(SNAPSHOTS_DIR)
    dst = os.path.join(
        SNAPSHOTS_DIR, "{p}_{v}.ecp".format(p=_snapshot_prefix(project),
                                            v=version))
    with open(src, "rb") as fh:
        data = fh.read()
    with open(dst, "wb") as fh:
        fh.write(data)
    log("OK", "snapshot: {s} -> {d} ({n} bytes, version {v}, "
              "saved_first={sv})".format(
        s=src, d=dst, n=len(data), v=version, sv=save_ok))
    return dst


def cmd_probe(project):
    """Dump dir() of the injected API objects to PROBE_OUT."""
    targets = [
        ("system", globals().get("system")),
        ("e_system", globals().get("e_system")),
        ("e_projects", globals().get("e_projects")),
        ("online (module)", globals().get("online")),
        ("project (primary)", project),
        ("Application", get_application(project)),
    ]
    try:
        for d in project.devices:
            targets.append(("device {d}".format(d=_short_name(d)), d))
    except Exception:
        pass
    interesting = []
    out = ["e!COCKPIT scripting API probe - {ts}".format(ts=_ts()), ""]
    for label, obj in targets:
        out.append("=" * 70)
        out.append("=== {l} ===".format(l=label))
        if obj is None:
            out.append("<not available>")
            continue
        try:
            names = sorted(n for n in dir(obj) if not n.startswith("_"))
        except Exception as exc:
            out.append("dir() failed: {e}".format(e=exc))
            continue
        out.append("{n} members:".format(n=len(names)))
        for n in names:
            out.append("    " + n)
        for n in names:
            if any(k in n.lower() for k in _PROBE_KEYWORDS):
                interesting.append("{l}: {n}".format(l=label, n=n))
    out.append("")
    out.append("=" * 70)
    out.append("=== Interesting members (keyword-filtered) ===")
    for line in interesting:
        out.append("    " + line)
    d = os.path.dirname(PROBE_OUT)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(PROBE_OUT, "wb") as fh:
        for line in out:
            fh.write(_to_bytes(line) + b"\n")
    log("OK", "probe written to {p} ({n} interesting members)".format(
        p=PROBE_OUT, n=len(interesting)))
    for line in interesting:
        log("INFO", "probe: {l}".format(l=line))

    # dir() is EMPTY on dynamic ScriptObjects (project/Application/device),
    # so probe curated method names via getattr instead. A hit like
    # generate_code/check_all would give us a scripted compile that EMITS
    # messages (create_boot_application dies before posting any).
    _method_probes = ("generate_code", "check_all", "check", "build",
                      "compile", "create_boot_application",
                      "generate_boot_project", "generate_code_current_app",
                      "login", "logout", "online_application",
                      "get_children", "children", "parent", "name",
                      "is_connected", "connected", "active_application")
    log("INFO", "getattr probe (dynamic objects):")
    for label, obj in (("project", project),
                       ("Application", get_application(project))):
        for name in _method_probes:
            try:
                v = getattr(obj, name)
                log("INFO", "probe-hit: {l}.{n} = {t}".format(
                    l=label, n=name, t=type(v).__name__))
            except Exception:
                continue

    # CODESYS-V3.5-howto API check (2026-10-20 owner paste): the article
    # claims system.get_message_log()/get_messages_count() and
    # project.get_active_application().get_build().clean()/execute()/
    # has_errors(). Probe whether this e!COCKPIT build exposes them.
    _article_system = ("get_message_log", "get_messages_count")
    sysobj = globals().get("system")
    if sysobj is not None:
        for name in _article_system:
            try:
                v = getattr(sysobj, name)
                log("INFO", "article-api-hit: system.{n} = {t}".format(
                    n=name, t=type(v).__name__))
            except Exception:
                log("INFO", "article-api-miss: system.{n}".format(n=name))
    _article_project = ("get_active_application", "get_build", "save",
                        "save_as", "get_name")
    _article_app = ("get_build", "get_name", "activate_application")
    for label, obj, names in (("project", project, _article_project),
                              ("Application", get_application(project),
                               _article_app)):
        for name in names:
            try:
                v = getattr(obj, name)
                log("INFO", "article-api-hit: {l}.{n} = {t}".format(
                    l=label, n=name, t=type(v).__name__))
                if name == "get_build":
                    try:
                        b = v()
                        for bn in ("clean", "execute", "has_errors",
                                   "is_busy", "result"):
                            try:
                                bv = getattr(b, bn)
                                log("INFO", "article-api-hit: {l}.get_build().{n} = {t}".format(
                                    l=label, n=bn, t=type(bv).__name__))
                            except Exception:
                                log("INFO", "article-api-miss: {l}.get_build().{n}".format(
                                    l=label, n=bn))
                    except Exception as exc:
                        log("INFO", "article-api: {l}.get_build() call failed: {e}".format(
                            l=label, e=exc))
            except Exception:
                log("INFO", "article-api-miss: {l}.{n}".format(l=label, n=name))


# --------------------------------------------------------------------------
# Command dispatch
# --------------------------------------------------------------------------
KNOWN_COMMANDS = ("build", "import", "compile", "status", "read",
                  "download", "disconnect", "probe", "messages", "snapshot")


def run_command(request):
    """Execute one request {"id","cmd","args"} and return the result dict.

    Always flushes tmp/ecockpit_build.log with a RESULT= line, even on a
    crashed command, so no waiting client ever stalls.
    """
    cmd = request.get("cmd", "build")
    args = request.get("args", {}) or {}
    rid = request.get("id", "")
    del _lines[:]
    summary = {"ok": False, "errors": 0, "warnings": 0, "imported": 0, "skipped": 0}
    result = {"id": rid, "cmd": cmd, "ok": False, "value": None, "error": "",
              "log": LOG_FILE}
    log("INFO", "command '{c}' (id {i}) starting".format(c=cmd, i=rid or "<direct>"))

    try:
        if cmd in ("build", "import", "compile"):
            project = get_project()
            app = get_application(project)
            if cmd in ("build", "import") and not (args.get("skip_import") or SKIP_IMPORT):
                import_xml_files(app, summary)
                cleanup_removed_objects(project, summary)
                # Let e!COCKPIT settle the project tree after imports.
                system.delay(500)
            else:
                log("INFO", "import step skipped for '{c}'".format(c=cmd))
            if cmd in ("build", "compile"):
                compile_project(app, project, summary, args)
            else:
                summary["ok"] = summary["errors"] == 0
        elif cmd == "status":
            project = get_project()
            result["value"] = cmd_status(project)
            summary["ok"] = True
        elif cmd == "read":
            project = get_project()
            result["value"] = cmd_read(project, args)
            summary["ok"] = True
        elif cmd == "download":
            project = get_project()
            cmd_download(project, args)
            summary["ok"] = True
        elif cmd == "disconnect":
            project = get_project()
            cmd_disconnect(project, args)
            summary["ok"] = True
        elif cmd == "probe":
            project = get_project()
            cmd_probe(project)
            summary["ok"] = True
        elif cmd == "messages":
            # System-level: no project lookup needed.
            result["value"] = cmd_messages()
            summary["ok"] = True
        elif cmd == "snapshot":
            project = get_project()
            result["value"] = cmd_snapshot(project, args)
            summary["ok"] = True
        else:
            raise RuntimeError("unknown command '{c}' (known: {k})".format(
                c=cmd, k=", ".join(KNOWN_COMMANDS)))
    except Exception:
        summary["ok"] = False
        summary["errors"] += 1
        result["error"] = traceback.format_exc()
        log("ERROR", "command '{c}' failed:\n{tb}".format(
            c=cmd, tb=result["error"]))

    # Import errors must fail the command even when the compile succeeded:
    # a rejected import means eCockpit compiled the PREVIOUS object state
    # ("used on a device you are currently logged into" - logout, rebuild).
    if summary["errors"] > 0:
        summary["ok"] = False
    if summary["errors"] > 0 and result["error"] == "":
        result["error"] = "{n} import/cleanup error(s) - see log".format(
            n=summary["errors"])

    flush_log(rid, cmd, summary)
    result["ok"] = bool(summary["ok"])
    # Machine-readable enrichment (consumed by the ecockpit-mcp server).
    result["errors"] = summary["errors"]
    result["warnings"] = summary["warnings"]
    result["summary_line"] = "{c} {ok} | errors={e} warnings={w} imports={i}".format(
        c=cmd, ok="OK" if summary["ok"] else "FAILED",
        e=summary["errors"], w=summary["warnings"], i=summary["imported"])
    result["harvest"] = summary.get("harvest", [])
    result["ignored"] = summary.get("harvest_ignored", {})
    return result


# Direct run (FILE -> Scripting -> Execute script file): execute a BUILD.
# The command server instead sets ECP_NO_RUN=1 and calls run_command()
# itself once per request.
if not os.environ.get("ECP_NO_RUN"):
    run_command({"id": "", "cmd": "build", "args": {}})
