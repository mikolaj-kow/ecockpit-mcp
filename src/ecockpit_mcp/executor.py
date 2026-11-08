# -*- coding: utf-8 -*-
# This comment is required if there are any non-ASCII characters in the script file.
"""
eCockpit command EXECUTOR - runs INSIDE e!COCKPIT's Python Scripting engine.

It is NOT a standalone script: it relies on globals injected by e!COCKPIT
(e_projects, e_system, system, Severity, ImportReporter, ConflictResolve,
online). It is normally driven by relay.py, which exec()s this
file once per command and then calls run_command(request). It can also be
run directly (FILE -> Scripting -> Execute script file); a direct run
executes a BUILD.

Commands (request = {"id": ..., "cmd": ..., "args": {...}}):
    build      re-import XMLs + stale-POU cleanup + compile (default)
    import     re-import XMLs + cleanup only, no compile
    compile    compile only, no import (old --skip-import)
    rebuild    FULL rebuild (app.rebuild(); scripted Build -> Clean)
    status     project/application/device names, ip, simulated flag
    read       read one variable online.  args: {"path": "GVL.var"}
               (optional "device": name). Requires PLC credentials.
    write      write/force values online. args: {"values": {expr: literal},
               "force": bool, "confirm": true}. DANGEROUS - confirm-gated.
    unforce    release all forced values. args: {} (optional "device")
    download   connect, create boot application ON the PLC (replaces the
               running app!), start it, disconnect. args: {"confirm": true}
               and optional "device". Requires PLC credentials + confirm.
    disconnect log out of the PLC (optional "device", "force": true for
               forced_disconnect).
    stop       STOP the running PLC application. args: {"confirm": true}
    state      online state readout (login/app/operation state, device)
    messages   dump the message-store category map (scripting API debugging)
    snapshot   save + copy the open project to <snapshots>/<project>_<v>.ecp
    export     export the application to PLCopen XML. args: {"dir": ...}
    plcfs      PLC file system: args {"op": "ls"|"get"|"put"|"rm", ...};
               put/rm need "confirm": true (modify PLC files)
    upload_source  read the source project archive back from the PLC.
               args: {"path": host archive}
    save_archive   save the project as .projectarchive. args: {"path": ...}
    clear_messages  wipe the scripting message store (fresh-harvest aid)
    system     `system` object: info, write_message, prompt handling +
               answers (headless dialog simulation), abort/trace flags,
               progress bar. ui.* dialogs + exit are confirm-gated ops
               (wedge / kills-e!COCKPIT hazards)
    device_info  device identification / simulation mode / driver info
               (bus cycle task, IO application, stop behaviour,
               diagnosis); the setters are confirm-gated
    device_io  IO mapping inspection + CSV export/import
               (export/import_io_mappings_as_csv); set_mapping and
               import_csv are confirm-gated (modify the IO config)
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

Active-project switch (project_control op=use / use_clear): op=use
root=<path> validates {root, plcopensrc XML, .ecp}, optionally opens the
.ecp as primary and persists <ECP_WORK_DIR>/ecockpit_active_project.json,
which OVERRIDES ECP_PROJECT_ROOT/ECP_XML_DIR until op=use_clear.
Precedence: scriptargs > sidecar > .env > discovery. ecockpit_status
reports the active triplet + its source in the project_sources block.

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
import time
import traceback
import datetime
import xml.etree.ElementTree as ET


# --------------------------------------------------------------------------
# ScriptEngine injected globals
# --------------------------------------------------------------------------
# e!COCKPIT's ScriptEngine injects these into every script's globals before
# execution; under plain CPython (flake8, offline import, tests) they do not
# exist. The guarded form keeps the injected originals when present and only
# binds stand-ins when it is NOT running inside e!COCKPIT, so the module
# stays lintable and importable offline. None of the stand-ins are usable -
# every code path that touches them needs the real engine anyway.
if "system" not in globals():
    system = None
if "Severity" not in globals():
    Severity = None
if "script_arguments" not in globals():
    script_arguments = ""
if "online" not in globals():
    online = None
if "e_projects" not in globals():
    e_projects = None
if "librarymanager" not in globals():
    librarymanager = None
if "ArchiveCategories" not in globals():
    # IScriptProjectArchiveCategories - injected per CHM ("An instance of
    # this object is injected into the scriptengine module with the name
    # ArchiveCategories"); its .default/.none members give save_archive a
    # TYPED category that disambiguates the IronPython overload maze.
    ArchiveCategories = None
if "OnlineChangeOption" not in globals():
    # _3S.CoDeSys.OnlineUI.OnlineChangeOption, injected like Severity (per
    # CHM): Never (0, online change never -> full download forced), Try
    # (1, online change if possible else full download), Force (2, online
    # change or nothing). Keep (3, stub-only): login without online
    # update or download.
    OnlineChangeOption = None
if "PromptHandling" not in globals():
    # ScriptUI prompt simulation enum (ScriptSystem stub); LogSimplePrompts
    # keeps simple prompts out of the GUI and into the message store.
    PromptHandling = None
if "PromptResult" not in globals():
    # PromptResult: OK=0, Cancel=1, Yes=2, No=3, Retry=4, Abort=5, Ignore=6
    # (ScriptSystem stub) - values for system.prompt_answers simulation.
    PromptResult = None
if "PromptChoice" not in globals():
    # PromptChoice: OK=0, OKCancel=1, YesNo=2, YesNoCancel=3, RetryCancel=4,
    # AbortRetryIgnore=5 (ScriptSystem stub) - system.ui.prompt layouts.
    PromptChoice = None
if "PouType" not in globals():
    # ST object creation (ScriptIecLanguageObjectContainer stub):
    # Program=1, FunctionBlock=2, Function=3.
    PouType = None
if "DutType" not in globals():
    # DutType: Structure/Enumeration/Alias/Union/EnumerationWithTextList.
    DutType = None
if "SpecialPouType" not in globals():
    # SpecialPouType (ScriptIecLanguageObjectContainer stub, 3.5.12.10):
    # implicit-check POUs - none=1, check_bounds=2, check_pointer=3,
    # check_range_unsigned=4, check_range_signed=5, check_div_real_64=6,
    # check_div_real_32=7, check_div_int_64=8, check_div_int_32=9,
    # check_l_range_unsigned=10, check_l_range_signed=11.
    SpecialPouType = None
if "KindOfTask" not in globals():
    # KindOfTask: Cyclic=1, Freewheeling=2, Event=3, ExternalEvent=4,
    # Status=5, ParentSynchron=6.
    KindOfTask = None
if "StopResetBehaviour" not in globals():
    # ScriptDeviceObject stub: outputs behaviour on PLC stop -
    # KeepCurrentValues=0, SetToDefault=1, ExecuteProgram=2.
    StopResetBehaviour = None
if "AlwaysUpdateVariablesMode" not in globals():
    # ScriptDriverInfo stub: always-update-variables mode -
    # Disabled=1, OnlyIfUnused=2, AlwaysInBusCycle=3.
    AlwaysUpdateVariablesMode = None
if "SymbolConfigContentFeatureFlags" not in globals():
    # ScriptSymbolConfigObject stub: symbol XML content flags
    # (SupportOPCUA=1, IncludeComments=2, ...; Xml* live above bit 16).
    SymbolConfigContentFeatureFlags = None
if "SymbolAttributeFilterTypes" not in globals():
    # ScriptSymbolConfigObject stub: none=0, All=1, SimpleIdentifiers=2,
    # Prefix=3, Regex=4.
    SymbolAttributeFilterTypes = None
if "SymbolCommentFilterType" not in globals():
    # ScriptSymbolConfigObject stub: none=0, NormalComments=1,
    # DocuComments=2, Both=3, PreferNormalComments=4,
    # PreferDocuComments=5.
    SymbolCommentFilterType = None
if "ImplementationLanguages" not in globals():
    # ST/CFC/FBD/... implementation language GUIDs (injected global).
    ImplementationLanguages = None
if "ResetOption" not in globals():
    # Online application reset options: Warm=0, Cold=1, Original=2
    # (Original erases applications + boot project + retain data).
    ResetOption = None
if "projects" not in globals():
    # CHM/stub name for IScriptProjects; this live build injects
    # e_projects (the executor's get_project has used it all along) -
    # cmd_project_control resolves whichever exists via
    # _projects_global().
    projects = None
if "ConflictResolve" not in globals():
    ConflictResolve = None
if "ImportReporter" not in globals():
    class ImportReporter(object):
        """Placeholder base; the real class only exists inside e!COCKPIT."""
if "ExportReporter" not in globals():
    # LIVE GOTCHA (2026-10-21): the engine exposes the interface under the
    # name ExportReporter - "IExportReporter" is only the .NET type name
    # and is NEVER injected, so guarding on it alone left the placeholder
    # bound live and export_xml rejected our reporter
    # ("expected IExportReporter, got Object_1$1").
    ExportReporter = None
if "IExportReporter" not in globals():
    class IExportReporter(object):
        """Placeholder base; the real class only exists inside e!COCKPIT."""


_headless_done = [False]


def _headless_hardening():
    """ScriptSystem stub: the relay cannot answer GUI dialogs, so simple
    prompts are logged into the message store instead of popping up and
    wedging the GUI (see the PLC-dialog wedge hazard). Runs once per
    executor process (the relay re-execs this file per command)."""
    if _headless_done[0]:
        return
    _headless_done[0] = True
    try:
        if PromptHandling is not None:
            system.prompt_handling = PromptHandling.LogSimplePrompts
        else:
            system.prompt_handling = 2  # PromptHandling.LogSimplePrompts
        log("INFO", "headless: simple prompts are logged, not shown "
            "(prompt_handling=LogSimplePrompts)")
    except Exception as exc:
        log("WARN", "headless hardening unavailable: {e}".format(e=exc))


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

    When exec()'d by relay.py, __file__ is the SERVER's path
    (this file lives in the same directory), so the .env lookup below lands
    on the ecockpit-mcp .env in both direct-run and server-driven mode.
    """
    try:
        return os.path.dirname(os.path.abspath(__file__))
    except NameError:
        return os.getcwd()


def _find_dotenv(start_dir):
    """Locate the one project .env: next to the scripts, then up to two
    parent levels (the src/ package layout sits two levels below the repo
    root). Anchored at __file__ - never the cwd - so the daemon, the armed
    command relay, and the executor all resolve the SAME file.
    """
    d = os.path.abspath(start_dir)
    seen = []
    for _ in range(3):
        seen.append(os.path.join(d, ".env"))
        d = os.path.dirname(d)
    for path in seen:
        if os.path.isfile(path):
            return path
    return seen[0]


ENV_FILE = os.environ.get("ECP_ENV_FILE", "") \
    or _find_dotenv(_this_dir())
_ENV = _load_dotenv(ENV_FILE)


def _cfg(name, default=""):
    """ECP_* setting: environment variable wins over the .env file."""
    return os.environ.get(name, "") or _ENV.get(name, "") or default


# Warnings raised before log() exists (the module-init sidecar read below
# runs at import time); drained by the first log() call into the normal
# log channels (message window + command log).
_EARLY_WARNS = []


def _active_project():
    """Read the op=use active-project sidecar, or None when absent.

    Sidecar schema: {"project_root", "xml_dir", "ecp", "set_at",
    "source": "op=use"}. Corrupt/incomplete -> None plus a WARN (deferred
    via _EARLY_WARNS), never a raise - a broken switch must fall back to
    the .env config, loudly. io.open+json like the PLC-state sidecar;
    IronPython 2.7 compatible.
    """
    if not os.path.isfile(ACTIVE_PROJECT_FILE):
        return None
    try:
        with io.open(ACTIVE_PROJECT_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict) or not data.get("project_root"):
            raise ValueError("missing project_root")
        return data
    except Exception as exc:
        _EARLY_WARNS.append(
            "active-project sidecar unreadable - falling back to the .env "
            "config ({p}): {e}".format(p=ACTIVE_PROJECT_FILE, e=exc))
        return None


def _apply_active_project(active):
    """Module-init override: the sidecar's project_root replaces the .env
    root. Called right after PROJECT_ROOT = _cfg(...) and BEFORE the
    derived constants (DEFAULT_XML, snapshots/projects fallbacks) are
    computed, so they follow the override automatically."""
    global PROJECT_ROOT
    if active and active.get("project_root"):
        PROJECT_ROOT = str(active["project_root"])


# File-protocol + logs work dir (shared with the other ecockpit-mcp parts).
# Must be resolved BEFORE the project root: the active-project sidecar
# below lives here.
WORK_DIR       = _cfg("ECP_WORK_DIR", "") or os.path.join(_this_dir(), "tmp")
LOG_DIR        = os.path.join(WORK_DIR, "logs")
DEFAULT_LOG    = os.path.join(LOG_DIR, "ecockpit_build.log")
DEFAULT_BOOT   = os.path.join(WORK_DIR, "bootapp.app")
PROBE_OUT      = os.path.join(LOG_DIR, "ecockpit_api_probe.txt")
MSGS_DUMP_PATH = os.path.join(LOG_DIR, "ecockpit_messages_dump.txt")

# PLC connection sidecar: rewritten by _note_plc on every REAL connection
# attempt (the _online_connect login ladder; pre-login refusals like G-27
# never touch the PLC and must not record here). The MCP server's
# ecockpit_server_status reads it for the "plc" section.
PLC_STATE_FILE = os.path.join(WORK_DIR, "ecockpit_plc_state.json")

# Active-project sidecar: written by project_control op=use (cleared by
# op=use_clear). When present it OVERRIDES the .env's ECP_PROJECT_ROOT /
# ECP_XML_DIR - the sequential project/worktree switch. The relay re-execs
# this file per command, so a switch takes effect on the next command
# without a daemon restart or a re-arm. One sidecar by design: ONE
# e!COCKPIT instance, ONE primary project - parallel worktrees are an
# engine limit (single-writer protocol files, one GUI project slot).
ACTIVE_PROJECT_FILE = os.path.join(WORK_DIR, "ecockpit_active_project.json")

# Automation project repo: plcopensrc/ XML sources, .ecp discovery, default
# snapshots dir. NO default is built in on purpose - this executor is
# project-agnostic.
PROJECT_ROOT   = _cfg("ECP_PROJECT_ROOT", "")

# op=use override (sequential project/worktree switch): the sidecar's root
# replaces the .env root BEFORE the derived defaults below are computed, so
# they follow the override automatically. Explicit ECP_SNAPSHOTS_DIR /
# ECP_PROJECTS_DIR keys stay as configured - only root + xml_dir switch.
ACTIVE_PROJECT = _active_project()
_apply_active_project(ACTIVE_PROJECT)

# Session login cache: survives per-command execs inside ONE armed relay
# session (the relay execs executor.py into its own globals, so the
# guarded fetch below keeps the previous command's entry). Holds the
# onlineapp/device of a keep-login read so batch and follow-up reads
# reuse ONE login instead of a full login ladder each time. Single
# slot: one GUI, one primary project, one login. Cleared by an explicit
# disconnect and by any non-keep online command (fresh ladder first).
# A relay re-arm starts a new engine: the cache is empty then and the
# kept login is reachable only via disconnect (forced_logout).
_LOGIN_CACHE = globals().get("_LOGIN_CACHE") or {}
globals()["_LOGIN_CACHE"] = _LOGIN_CACHE


# Project-tree mutations refuse while a login is live ("Cannot add an
# object because it affects a device you are currently logged into").
# None = the whole command mutates; (arg_key, values) = only when that
# arg picks a mutating op.
TREE_MUTATION_SPECS = {
    "build": None,
    "import": None,
    "object_create": None,
    "task_config": None,
    "textual": ("part", ("write",)),
    "symbol_config": ("op", ("create", "config_set")),
    "project_control": ("op", ("create", "open", "open_archive", "close",
                               "info_set", "use")),
}
_NOT_MUTATION = object()


def _logout_kept_for_mutation(cmd):
    """Log the KEPT session out before a project-tree mutation.

    The engine refuses mutations ('affects a device you are currently
    logged into') while any login is live - including our keep-login
    read sessions. Explicit logout first, so the mutation runs on a
    clean project; the next read does a fresh Keep login (and is G-27
    refused until the change is synced - correct)."""
    entry = _LOGIN_CACHE.get("entry")
    if entry is None:
        return
    log("INFO", "{c}: logging out the kept session first (project "
        "mutation needs no live login)".format(c=cmd))
    _online_disconnect(entry["device"])
    _LOGIN_CACHE.clear()
    try:
        _note_plc(False, "logged out for project mutation ({c})".format(
            c=cmd), entry["device"])
    except Exception:
        pass


def _logout_kept_if_mutation(cmd, args):
    """Dispatch-side gate: logout the kept session only for the
    commands/ops that actually mutate the project tree
    (see TREE_MUTATION_SPECS)."""
    spec = TREE_MUTATION_SPECS.get(cmd, _NOT_MUTATION)
    if spec is _NOT_MUTATION:
        return
    if spec is None:
        _logout_kept_for_mutation(cmd)
        return
    key, values = spec
    if str(args.get(key, "")) in values:
        _logout_kept_for_mutation(cmd)

DEFAULT_XML    = os.path.join(PROJECT_ROOT, "plcopensrc") if PROJECT_ROOT else ""

# Appended to every escalated login failure. Worded conditionally on
# purpose: "all forms failed" also fires when the PLC is simply powered
# off - the attempt lines above the hint tell the reader which case holds.
LOGIN_FAIL_HINT = (
    "HINT: if the attempts above show 'Login failed' (not timeouts or "
    "unreachable), this is the known build limitation: online change is "
    "not possible on this PFC200 build - the s4 fLog logging funnel "
    "starves online change of a task gap (G-27/G-31 era findings, "
    "final 2026-10-22..24). Deploy with ecockpit_download (full "
    "download: reliable, carries edits) or quiesce logging first "
    "(GVLLogging.eGlobalLogLevel := LOG_EMERG) then retry option=try. "
    "Current PLC reachability: ecockpit_server_status, 'plc' section.")

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


def _ecp_candidates(root):
    """All .ecp files under root (top level + one level deep), minus the
    series_750 demo projects."""
    hits = list(glob.glob(os.path.join(root, "*.ecp"))) + \
           list(glob.glob(os.path.join(root, "*", "*.ecp")))
    return [h for h in hits
            if not os.path.basename(h).lower().startswith("series_750")]


def discover_project_file(root=None):
    """Find a single .ecp under the project root (one level deep). Empty
    when ambiguous or none. root defaults to PROJECT_ROOT (the op=use
    switch passes an explicit root)."""
    if root is None:
        root = PROJECT_ROOT
    if not os.path.isdir(root):
        return ""
    hits = _ecp_candidates(root)
    if len(hits) == 1:
        return hits[0]
    if len(hits) > 1:
        log("WARN", "multiple .ecp files found, please pass --project: "
                    + ", ".join(os.path.basename(h) for h in hits))
    return ""


def discover_xml_dir(root=None):
    """Locate the dir holding Application.*.xml under the project's
    plcopensrc/ - directly or one level deep (e.g. plcopensrc/<device>/).
    Empty string when not found. root defaults to PROJECT_ROOT."""
    if root is None:
        root = PROJECT_ROOT
    if not root:
        return ""
    base = os.path.join(root, "plcopensrc")
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
    active = ACTIVE_PROJECT or {}
    cfg = {
        # Precedence: scriptargs > active-project sidecar (op=use) >
        # .env (ECP_*) > discovery > derived default.
        "project":  args.get("project") or active.get("ecp")
                    or _cfg("ECP_PROJECT", "") or discover_project_file(),
        "xml_dir":  args.get("xml-dir") or active.get("xml_dir")
                    or _cfg("ECP_XML_DIR", "") or discover_xml_dir()
                    or DEFAULT_XML,
        "log":      args.get("log")     or _cfg("ECP_LOG", DEFAULT_LOG),
        "bootapp":  args.get("bootapp") or _cfg("ECP_BOOTAPP", DEFAULT_BOOT),
        "skip_import": args.get("skip_import", False)
                    or _cfg("ECP_SKIP_IMPORT", "0") == "1",
    }
    if not cfg["project"]:
        if globals().get("RELAY_DISPATCH"):
            # the relay FORCES PROJECT_FILE="" (it wants the GUI-open
            # primary project) - this fallback is the design, not a
            # misconfiguration, so it must not spam the message window
            # as a warning on every command
            log("INFO", "no --project given; relay dispatch uses the "
                        "already-open primary project")
        else:
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
    # Config warnings queued before this function existed (module-init
    # active-project sidecar read) go out first, in order.
    while _EARLY_WARNS:
        log("WARN", _EARLY_WARNS.pop(0))
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


def _project_source():
    """Where the active project root came from: the op=use sidecar or the
    .env (audit string for the build-log header and cmd_status)."""
    return "sidecar" if ACTIVE_PROJECT else ".env"


def _project_sources():
    """The active config triplet {project_root, xml_dir, .ecp} + its
    source, for cmd_status's project_sources block and the build-log
    header: drift between the .env, the op=use sidecar and the GUI-open
    primary must stay visible in every command (the 'open project from A
    + import XML from B' incident class)."""
    return {
        "project_root": PROJECT_ROOT or None,
        "xml_dir": XML_DIR or None,
        "source": _project_source(),
        "ecp_discovered": PROJECT_FILE or None,
    }


def _warn_primary_mismatch(project):
    """Audit guard for the op=use switch: WARN (never refuse) when the
    GUI-open primary project is NOT the .ecp the active-project sidecar
    recorded - imports land in whatever the GUI holds as primary, so an
    'open project from A + XML from B' state must at least be loud.
    Best effort: any probe failure stays silent."""
    if not ACTIVE_PROJECT:
        return
    sidecar_ecp = str(ACTIVE_PROJECT.get("ecp") or "")
    if not sidecar_ecp:
        return
    try:
        p = str(project.path or "")
    except Exception:
        return
    if not p:
        return
    if os.path.basename(p).lower() != os.path.basename(sidecar_ecp).lower():
        log("WARN", "primary project mismatch: open '{o}' vs active-project "
                    "sidecar '{s}' - the import goes into the OPEN project; "
                    "realign with project_control op=open".format(
                        o=os.path.basename(p), s=os.path.basename(sidecar_ecp)))


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
        # The active triplet {root, xml_dir, .ecp} + its source: every
        # command stays self-describing about WHICH project it touches.
        _w(fh, "root      : {r} (source: {s})".format(
            r=PROJECT_ROOT or "<unset>", s=_project_source()))
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
    if ln.startswith("[relay]") or ln.startswith("[server]") or ln.startswith("[watch]"):
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
# System domain (ScriptSystem stub: the injected `system` object)
# --------------------------------------------------------------------------
# Stub-derived value tables (ScriptSystem.pyi). They decode enum/flag
# values into names and build values when the injected enum global is
# unavailable; injected globals are preferred at runtime.
_PROMPT_HANDLING_FLAGS = (
    ("ForwardSimplePrompts", 1),
    ("LogSimplePrompts", 2),
    ("EnableTextPrompts", 4),
    ("ProcessScriptPrompts", 8),
    ("LogMessageKeys", 16),
)
_PROMPT_RESULT_VALUES = (
    ("OK", 0), ("Cancel", 1), ("Yes", 2), ("No", 3),
    ("Retry", 4), ("Abort", 5), ("Ignore", 6),
)
_PROMPT_CHOICE_VALUES = (
    ("OK", 0), ("OKCancel", 1), ("YesNo", 2), ("YesNoCancel", 3),
    ("RetryCancel", 4), ("AbortRetryIgnore", 5),
)
_SEVERITY_VALUES = (
    ("FatalError", 1), ("Error", 2), ("Warning", 4),
    ("Information", 8), ("Text", 16),
)


def _flags_to_names(value, flags):
    """Decode an int flag combination into its stub names (+ hex rest)."""
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return [str(value)]
    if iv == 0:
        return ["NONE"]
    out = []
    rest = iv
    for name, bit in flags:
        if bit and (rest & bit) == bit:
            out.append(name)
            rest -= bit
    if rest:
        out.append("0x{0:x}".format(rest))
    return out


def _enum_to_name(value, values):
    """Map one enum int onto its stub name (str(value) when unknown)."""
    try:
        iv = int(value)
    except (TypeError, ValueError):
        return str(value)
    for name, num in values:
        if num == iv:
            return name
    return str(iv)


def _enum_by_names(label, names, values, injected):
    """Resolve enum member names to one (combined) value.

    Prefers the injected enum global (real members; getattr on injected
    enums is established - cmd_object_create resolves PouType that way),
    falls back to the stub int table when the global is a stand-in.
    """
    known = ", ".join(name for name, _ in values)
    if injected is None:
        combined = 0
        for name in names:
            table = dict(values)
            if name not in table:
                raise RuntimeError("unknown {t} '{n}' (known: {k})".format(
                    t=label, n=name, k=known))
            combined |= table[name]
        return combined
    combined = None
    table = dict(values)
    for name in names:
        try:
            member = getattr(injected, name)
        except Exception:
            if name in table:
                # LIVE 2026-10-22 (G-20): the live enum can LACK a member
                # the stub documents (SymbolCommentFilterType has no
                # 'none'). Build a real enum instance via CLR
                # Enum.ToObject - the typed property REJECTS raw ints
                # ("expected SymbolCommentFilterType, got int"); raw int
                # is the last-resort fallback only.
                member = None
                try:
                    from System import Enum as _DotNetEnum
                    member = _DotNetEnum.ToObject(injected, table[name])
                except Exception:
                    member = table[name]
            else:
                raise RuntimeError("unknown {t} '{n}' (known: {k})".format(
                    t=label, n=name, k=known))
        combined = member if combined is None else (combined | member)
    return combined


def _system_ui_call(sysobj, args):
    """One system.ui method call - ALL of them block on a real modal
    dialog that the owner answers in the GUI (the caller enforces
    confirm). Password/credential results go back to the client in the
    result JSON but are NEVER logged (no-credentials-in-logs rule); they
    also pass through the result file unencrypted on this host.
    """
    try:
        ui = sysobj.ui
    except Exception as exc:
        raise RuntimeError("system.ui not available: {e}".format(e=exc))

    def _opt_str(name):
        v = args.get(name, "")
        if v is None or str(v) == "":
            return None
        return str(v)

    def _opt_int(name):
        v = args.get(name, None)
        if v is None or v == "":
            return None
        return int(v)

    method = str(args.get("method", "")).strip().lower()
    message = str(args.get("message", "") or "")
    message_key = str(args.get("message_key", "") or "ScriptMessage")
    sensitive = False
    res = None
    if method in ("error", "warning", "info"):
        if not message:
            raise RuntimeError("ui {m} needs args.message".format(m=method))
        if method == "error":
            res = ui.error(message, message_key)
        elif method == "warning":
            res = ui.warning(message, message_key)
        else:
            res = ui.info(message, message_key)
    elif method == "prompt":
        if not message:
            raise RuntimeError("ui prompt needs args.message")
        choice = _enum_by_names("PromptChoice",
                                [str(args.get("choice", "YesNo"))],
                                _PROMPT_CHOICE_VALUES, PromptChoice)
        default = _enum_by_names("PromptResult",
                                 [str(args.get("default_result", "No"))],
                                 _PROMPT_RESULT_VALUES, PromptResult)
        res = ui.prompt(message, choice, default,
                        _opt_str("store_description"), _opt_str("store_key"),
                        message_key)
    elif method == "select_many":
        items = args.get("items") or []
        if not message or not items:
            raise RuntimeError("ui select_many needs args.message and "
                               "args.items (list)")
        choice = _enum_by_names("PromptChoice",
                                [str(args.get("choice", "YesNo"))],
                                _PROMPT_CHOICE_VALUES, PromptChoice)
        default = _enum_by_names("PromptResult",
                                 [str(args.get("default_result", "OK"))],
                                 _PROMPT_RESULT_VALUES, PromptResult)
        res = ui.select_many(message, choice, default, list(items),
                             message_key)
    elif method == "choose":
        options = args.get("options") or []
        if not message or not options:
            raise RuntimeError("ui choose needs args.message and "
                               "args.options (list)")
        res = ui.choose(message, list(options),
                        int(args.get("initial_selection", 0) or 0),
                        bool(args.get("cancellable", True)), message_key)
    elif method == "reset_stored_result":
        if not args.get("store_key"):
            raise RuntimeError("ui reset_stored_result needs "
                               "args.store_key")
        res = ui.reset_stored_result(str(args["store_key"]))
    elif method == "open_file_dialog":
        res = ui.open_file_dialog(
            _opt_str("title"), _opt_str("filename"), _opt_str("directory"),
            _opt_str("filter"), _opt_int("filter_index"),
            _opt_str("default_extension"), None,
            bool(args.get("multiselect", False)))
    elif method == "save_file_dialog":
        res = ui.save_file_dialog(
            _opt_str("title"), _opt_str("filename"), _opt_str("directory"),
            _opt_str("filter"), _opt_int("filter_index"),
            _opt_str("default_extension"))
    elif method == "browse_directory_dialog":
        res = ui.browse_directory_dialog(
            message or "Select a directory", _opt_str("path") or "",
            _opt_str("root_folder") or "Environment.SpecialFolder."
            "MyComputer", bool(args.get("new_folder_button", True)))
    elif method == "query_string":
        res = ui.query_string(message, str(args.get("text", "") or ""),
                              bool(args.get("multi_line", False)),
                              bool(args.get("cancellable", False)))
    elif method == "query_password":
        sensitive = True
        res = ui.query_password(message, str(args.get("password", "") or ""),
                                bool(args.get("cancellable", False)))
    elif method == "query_credentials":
        sensitive = True
        res = ui.query_credentials(message,
                                   str(args.get("username", "") or ""),
                                   str(args.get("password", "") or ""),
                                   bool(args.get("cancellable", False)))
    else:
        raise RuntimeError(
            "unknown ui method '{m}' (known: error, warning, info, "
            "prompt, select_many, choose, reset_stored_result, "
            "open_file_dialog, save_file_dialog, "
            "browse_directory_dialog, query_string, query_password, "
            "query_credentials)".format(m=method))
    if isinstance(res, (list, tuple)):
        res = [str(x) for x in res]
    elif res is not None:
        res = str(res)
    log("OK", "ui {m}: {d}".format(
        m=method, d="completed (value not logged)" if sensitive else "done"))
    return {"method": method, "result": res}


def cmd_system(args):
    """Access the injected `system` object (ScriptSystem stub).

    ops:
      info                 -> ui_present, prompt_handling (decoded),
                              abort/trace flags, prompt_answers keys,
                              commands + factories inventory (read-only)
      write_message        {severity?: FatalError|Error|Warning|
                            Information|Text, text}
      set_prompt_handling  {value: flag names '+'-combined} - sets the
                            process-wide prompt policy; confirm needed
                            only when the result enables
                            ForwardSimplePrompts (dialogs back on)
      prompt_answers       -> the headless dialog-simulation answers
                              (read-only)
      set_prompt_answer    {key, result: OK|Cancel|Yes|No|Retry|Abort|
                            Ignore} -> pre-answer one prompt message key
      clear_prompt_answers -> empty the simulation dict
      set_flags            {trace?, abortable?, abort_autocheck?}
      progress             {action: start|step|absolute, ...} - e!COCKPIT
                            progress bar (GUI chrome only)
      ui                   {method, ..., confirm} - REAL modal dialogs,
                            wedge hazard, CONFIRM-GATED
      exit                 {code?, confirm} - kills e!COCKPIT,
                            CONFIRM-GATED

    PROMPT-HANDLING LIFETIME: _headless_hardening re-applies
    LogSimplePrompts at the start of EVERY command (the relay re-execs
    this file per command), so a set_prompt_handling value lasts only
    for the remainder of the CURRENT command - pre-arming dialogs for a
    later command is impossible by design; prompt_answers DO persist
    across commands (they live on the engine's system object).
    """
    op = args.get("op", "info")
    sysobj = globals().get("system")
    if sysobj is None:
        raise RuntimeError("system object not available (not running "
                           "inside e!COCKPIT?)")

    if op == "info":
        info = {}
        readers = (
            ("ui_present", lambda: bool(sysobj.ui_present)),
            ("executable_filename", lambda: str(sysobj.executable_filename)),
            ("trace", lambda: bool(sysobj.trace)),
            ("abortable", lambda: bool(sysobj.abortable)),
            ("aborting", lambda: bool(sysobj.aborting)),
            ("abort_autocheck", lambda: bool(sysobj.abort_autocheck)),
            ("background_loading_of_libraries_finished",
             lambda: bool(sysobj.background_loading_of_libraries_finished)),
        )
        for name, reader in readers:
            try:
                info[name] = reader()
            except Exception:
                info[name] = None
        try:
            info["prompt_handling"] = _flags_to_names(
                sysobj.prompt_handling, _PROMPT_HANDLING_FLAGS)
        except Exception:
            info["prompt_handling"] = None
        try:
            pa = sysobj.prompt_answers
            info["prompt_answer_keys"] = sorted(str(k) for k in pa.keys())
        except Exception:
            info["prompt_answer_keys"] = None
        cmds = []
        try:
            for c in sysobj.commands:
                try:
                    cmds.append(str(c.name))
                except Exception:
                    cmds.append(str(c))
                if len(cmds) >= 50:
                    break
        except Exception:
            cmds = None
        info["commands"] = cmds
        try:
            info["factory_count"] = len(sysobj.factories)
        except Exception:
            info["factory_count"] = None
        log("OK", "system info read")
        return info

    if op == "write_message":
        text = str(args.get("text", "") or "")
        if not text:
            raise RuntimeError("system write_message needs args.text")
        sev = _enum_by_names("Severity",
                             [str(args.get("severity", "Information"))],
                             _SEVERITY_VALUES, Severity)
        sysobj.write_message(sev, text)
        log("OK", "message written to the store")
        return {"written": True, "severity": str(args.get("severity",
                                                          "Information"))}

    if op == "set_prompt_handling":
        raw = str(args.get("value", "")).strip()
        if not raw:
            raise RuntimeError("system set_prompt_handling needs "
                               "args.value (e.g. 'LogSimplePrompts' or "
                               "'LogSimplePrompts+LogMessageKeys')")
        names = [t for t in re.split(r"[+,\s]+", raw) if t.strip()]
        if "NONE" in names:
            if len(names) != 1:
                raise RuntimeError("NONE cannot be combined with other "
                                   "PromptHandling flags")
            value = 0
            if PromptHandling is not None:
                try:
                    value = PromptHandling.NONE
                except Exception:
                    value = 0
        else:
            value = _enum_by_names("PromptHandling flag", names,
                                   _PROMPT_HANDLING_FLAGS, PromptHandling)
        try:
            enables_dialogs = bool(int(value) & 1)
        except (TypeError, ValueError):
            enables_dialogs = "ForwardSimplePrompts" in names
        if enables_dialogs and not args.get("confirm"):
            raise RuntimeError("set_prompt_handling REFUSED - "
                               "ForwardSimplePrompts re-enables GUI "
                               "dialogs (wedge hazard); pass "
                               "args.confirm=true only on an explicit "
                               "owner request")
        sysobj.prompt_handling = value
        try:
            effective = _flags_to_names(sysobj.prompt_handling,
                                        _PROMPT_HANDLING_FLAGS)
        except Exception:
            effective = None
        log("OK", "prompt_handling set to {n}".format(n=effective))
        return {"set": names, "effective": effective,
                "note": "re-hardened to LogSimplePrompts by the next "
                        "command"}

    if op == "prompt_answers":
        try:
            pa = sysobj.prompt_answers
            out = {}
            for k in pa.keys():
                try:
                    out[str(k)] = str(pa[k])
                except Exception:
                    out[str(k)] = None
        except Exception as exc:
            raise RuntimeError("prompt_answers unreadable: {e}".format(
                e=exc))
        log("OK", "prompt answers: {n} key(s)".format(n=len(out)))
        return {"count": len(out), "answers": out}

    if op == "set_prompt_answer":
        key = str(args.get("key", "")).strip()
        rname = str(args.get("result", "")).strip()
        if not key or not rname:
            raise RuntimeError("system set_prompt_answer needs args.key "
                               "(prompt message key) and args.result "
                               "(OK|Cancel|Yes|No|Retry|Abort|Ignore)")
        value = _enum_by_names("PromptResult", [rname],
                               _PROMPT_RESULT_VALUES, PromptResult)
        sysobj.prompt_answers[key] = value
        log("OK", "prompt answer set: {k} -> {r}".format(k=key, r=rname))
        return {"key": key, "result": rname,
                "note": "persists for this e!COCKPIT session; system.ui "
                        "dialog methods ignore it (per stub)"}

    if op == "clear_prompt_answers":
        try:
            pa = sysobj.prompt_answers
            n = len(pa)
            pa.clear()
        except Exception as exc:
            raise RuntimeError("prompt_answers.clear() failed: {e}".format(
                e=exc))
        log("OK", "prompt answers cleared ({n})".format(n=n))
        return {"cleared": n}

    if op == "set_flags":
        changed = {}
        if args.get("trace") is not None:
            sysobj.trace = bool(args["trace"])
            changed["trace"] = bool(args["trace"])
        if args.get("abortable") is not None:
            sysobj.abortable = bool(args["abortable"])
            changed["abortable"] = bool(args["abortable"])
        if args.get("abort_autocheck") is not None:
            sysobj.abort_autocheck = bool(args["abort_autocheck"])
            changed["abort_autocheck"] = bool(args["abort_autocheck"])
        if not changed:
            raise RuntimeError("system set_flags needs at least one of "
                               "trace, abortable, abort_autocheck")
        readback = {}
        for name, reader in (
                ("trace", lambda: sysobj.trace),
                ("abortable", lambda: sysobj.abortable),
                ("aborting", lambda: sysobj.aborting),
                ("abort_autocheck", lambda: sysobj.abort_autocheck)):
            try:
                readback[name] = bool(reader())
            except Exception:
                readback[name] = None
        log("OK", "system flags set: {c}".format(c=sorted(changed)))
        return {"set": changed, "flags": readback}

    if op == "progress":
        action = str(args.get("action", "")).strip().lower()
        if action == "start":
            sysobj.progress_start(
                str(args.get("description", "") or "working"),
                int(args.get("items", -1) or -1),
                str(args.get("unit", "") or ""))
        elif action == "step":
            sysobj.progress_step(str(args.get("item", "") or ""),
                                 int(args.get("completed", 1) or 1))
        elif action == "absolute":
            sysobj.progress_absolute(
                str(args.get("item", "") or ""),
                int(args.get("absolute_progress", 0) or 0))
        else:
            raise RuntimeError("system progress needs args.action of "
                               "start|step|absolute")
        log("OK", "progress {a}".format(a=action))
        return {"action": action}

    if op == "ui":
        if not args.get("confirm"):
            raise RuntimeError("system ui REFUSED - these methods block "
                               "on a real GUI dialog (wedge hazard); "
                               "pass args.confirm=true only on an "
                               "explicit owner request")
        return _system_ui_call(sysobj, args)

    if op == "exit":
        if not args.get("confirm"):
            raise RuntimeError("system exit REFUSED - it shuts down the "
                               "whole e!COCKPIT process (and the armed "
                               "relay); pass args.confirm=true only on "
                               "an explicit owner request")
        code = int(args.get("code", 0) or 0)
        log("OK", "system.exit({c}) - shutting e!COCKPIT down; no result "
            "file will be written for this command".format(c=code))
        sysobj.exit(code)
        return {"exiting": True, "code": code,
                "note": "process (and relay) gone - re-arm after "
                        "restarting e!COCKPIT"}

    raise RuntimeError('system needs args.op of "info", "write_message", '
                       '"set_prompt_handling", "prompt_answers", '
                       '"set_prompt_answer", "clear_prompt_answers", '
                       '"set_flags", "progress", "ui" or "exit"')


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


def _export_reporter_base():
    """Resolve the class export_xml reporters must subclass.

    The engine injects the interface as ExportReporter (CHM: "exposed
    under the name ExportReporter"); IExportReporter is only the .NET
    type name. Fall back across both names for robustness, then to a
    plain base for offline import (the factory is never CALLED offline).
    """
    for name in ("ExportReporter", "IExportReporter"):
        candidate = globals().get(name)
        if candidate is not None:
            return candidate
    return object


def make_export_reporter(summary):
    """Build an export_xml reporter funneling callbacks into our logfile.

    Factory (same pattern as make_import_reporter) so plain CPython can
    import this module offline; the real interface only exists inside
    e!COCKPIT (see _export_reporter_base for the naming gotcha).
    Callbacks per ScriptEngine.chm: error/warning(message),
    nonexportable(obj), aborting property. LIVE 2026-10-21: the EXPORT
    flow calls warning() with TWO message arguments
    ("warning() takes exactly 2 arguments (3 given)" with the CHM
    1-arg signature) - all callbacks therefore take *argv and use the
    first argument, so any engine arity flows into the log.
    """

    class AgentExportReporter(_export_reporter_base()):
        def __init__(self):
            self.file_errors = 0
            self.file_warnings = 0

        def error(self, *argv):
            self.file_errors += 1
            summary["errors"] += 1
            log("ERROR", "export: {m}".format(
                m=" | ".join(str(a) for a in argv)[:300]))

        def warning(self, *argv):
            self.file_warnings += 1
            summary["warnings"] += 1
            log("WARN", "export: {m}".format(
                m=" | ".join(str(a) for a in argv)[:300]))

        def nonexportable(self, *argv):
            log("WARN", "export: nonexportable {o}".format(
                o=_short_name(argv[0]) if argv else "<???>"))

        @property
        def aborting(self):
            return False

    return AgentExportReporter()


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


def compile_project(app, project, summary, args=None, rebuild=False):
    """Compile the application via the DOCUMENTED script API.

    Primary: IScriptApplication.build() (ScriptEngine.chm) - builds the
    application and posts the full compile narrative + structured
    IScriptMessage diagnostics (prefix/number/text/severity/object/
    position_text) into the message store, which we read right after.
    rebuild=True uses IScriptApplication.rebuild() instead - a full rebuild
    from scratch, the scripted equivalent of the GUI Build -> Clean + Build
    (fixes "out of global data memory" C0106 on grown GVL arrays).
    Fallback: create_boot_application(path) - legacy vehicle whose final
    step dies with a content-free NullReferenceException on compile errors.

    args.all_messages=true bypasses the IGNORE_CODES known-noise filter
    (.env) for this run; otherwise ignored codes drop out of the harvest
    and the counts, replaced by a one-line summary. args.harvest_lines
    (default 60) raises how many harvest lines land in the JSON result
    (the report file section follows at max(harvest_lines, 100)) -
    full-harvest parses no longer need a code change.
    """
    verb = "rebuild" if rebuild else "build"
    try:
        harvest_lines = int((args or {}).get("harvest_lines", 60))
    except (TypeError, ValueError):
        harvest_lines = 60
    harvest_lines = max(harvest_lines, 0)
    log("INFO", "compiling (app.{v}() - documented IScriptApplication API)"
        .format(v=verb))
    build_raised = None
    build_available = True
    try:
        if rebuild:
            app.rebuild()
        else:
            app.build()
        log("OK", "app.{v}() returned without exception".format(v=verb))
    except AttributeError:
        build_available = False
        log("WARN", "app.{v}() not available on this object - falling "
                    "back to create_boot_application".format(v=verb))
    except Exception as exc:
        build_raised = exc
        log("ERROR", "app.{v}() raised: {e}".format(v=verb, e=exc))
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
        harvest = lines[:harvest_lines]
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
        report_cap = max(harvest_lines, 100)
        _lines.append("----- Compiler messages (IScriptMessage, "
                      "structured) -----")
        for ln in lines[:report_cap]:
            _lines.append(ln)
        if len(lines) > report_cap:
            _lines.append("... {n} more".format(n=len(lines) - report_cap))
        _lines.append("")

    if build_available:
        if build_raised is None and s_errors == 0:
            summary["ok"] = True
            log("OK", "compile OK (app.{v}(), {e} errors / {w} warnings "
                      "in message store)".format(v=verb, e=s_errors,
                                                 w=s_warnings))
        else:
            summary["ok"] = False
            if build_raised is None:
                summary["errors"] += 1 if s_errors == 0 else 0
            log("ERROR", "compile FAILED (app.{v}(); store counts: "
                         "{e} errors / {w} warnings)".format(
                             v=verb, e=s_errors, w=s_warnings))
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
def _login_gap_timeout_recovery(onlineapp, device, engapp, option, label,
                                exc, delete_foreign_apps=False,
                                attempts=None):
    """Second chance after a login raised the CmpApp task-gap timeout.

    LIVE 2026-10-22 (VictoriaLogs codesys3 stream): CmpApp logs
    "OnlineChange failed, no task gap within 10000 ms" as an EXCEPTION
    mid-operation, then completes the change anyway - milliseconds later
    the same operation logs "App OnlineChange successful" plus a
    CodeGUID/DataGUID swap (9 timeouts vs 14 completions since 10-21,
    every timeout followed by a completion). The engine exception is
    therefore NOT the final verdict. Recovery: when the application
    turned uptodate, treat the login as applied; otherwise retry the
    SAME form once (the runtime is typically quiescent after the first
    attempt). Returns "applied" | "retry-ok" | None (caller keeps its
    failure path - escalate/raise as before).
    """
    if "task gap" not in str(exc).lower():
        return None
    if engapp is not None:
        try:
            if bool(engapp.is_uptodate):
                log("OK", "{l} raised the task-gap timeout but the runtime "
                    "APPLIED the change (is_uptodate=True; CmpApp logs the "
                    "timeout mid-operation and completes anyway) - "
                    "accepting without escalation".format(l=label))
                return "applied"
        except Exception:
            pass
    try:
        if option is None:
            device.connect()
        else:
            onlineapp.login(option, delete_foreign_apps)
    except Exception as exc2:
        if attempts is not None:
            attempts.append("{l} retry: {c}: {m}".format(
                l=label, c=type(exc2).__name__, m=str(exc2)[:160]))
        log("INFO", "{l} retry after task-gap timeout failed: {c}: {m}"
            .format(l=label, c=type(exc2).__name__, m=str(exc2)[:160]))
        return None
    log("OK", "{l} retry succeeded (clean second attempt after the "
        "task-gap timeout)".format(l=label))
    return "retry-ok"


def _online_login(onlineapp, device, prefer_keep=False, engapp=None,
                  forms=None):
    """Explicit login ladder - online change control.

    device.connect() performed an IMPLICIT login-with-download (live
    2026-10-21: a read-only plc_ls deployed the open, untested
    application to the live PLC). The controlled form is
    onlineapp.login(OnlineChangeOption, delete_foreign_apps) with the
    enum per CHM: Try (online change if possible, else full download),
    Force (online change or nothing), Never (full download forced).
    OnlineChangeOption.Keep (stub-only, NOT in the CHM: "Try to login.
    Do not online update. Do not download. Keep as it is.") is the
    safe login for read-only commands - enabled with prefer_keep.
    Default order: Try, Force, Never, then the legacy device.connect()
    fallback. Returns the form used; raises when every form fails.
    forms: explicit whitelist of form names (e.g. ["Force"] for the
    download-online strict ladder) - overrides the prefer_keep order.
    """
    options = []
    names = list(forms) if forms else (["Keep"] if prefer_keep
                                       else ["Try", "Force", "Never"])
    if OnlineChangeOption is not None:
        for name in names:
            try:
                if name == "Try":
                    options.append(("login(OnlineChangeOption.Try)",
                                    OnlineChangeOption.Try))
                elif name == "Force":
                    options.append(("login(OnlineChangeOption.Force)",
                                    OnlineChangeOption.Force))
                elif name == "Never":
                    options.append(("login(OnlineChangeOption.Never)",
                                    OnlineChangeOption.Never))
                else:
                    options.append(("login(OnlineChangeOption.Keep)",
                                    OnlineChangeOption.Keep))
            except Exception as exc:
                log("INFO", "OnlineChangeOption.{n} unavailable: "
                    "{e}".format(n=name, e=exc))
    if not prefer_keep and forms is None:
        options.append(("device.connect()", None))
    attempts = []
    for label, option in options:
        try:
            if option is None:
                device.connect()
            else:
                onlineapp.login(option, False)
            log("OK", "logged in via {l}".format(l=label))
            # Download mode routing reads this (start only after a FULL
            # form; online change leaves the application running).
            _LOGIN_CACHE["last_via"] = label
            return label
        except Exception as exc:
            attempts.append("{l}: {c}: {m}".format(
                l=label, c=type(exc).__name__, m=str(exc)[:160]))
            log("INFO", "login via {l} failed: {c}: {m}".format(
                l=label, c=type(exc).__name__, m=str(exc)[:160]))
            recovered = _login_gap_timeout_recovery(
                onlineapp, device, engapp, option, label, exc,
                attempts=attempts)
            if recovered == "applied":
                return label + " [task-gap timeout, change applied]"
            if recovered == "retry-ok":
                return label + " [retry after task-gap timeout]"
    if prefer_keep:
        raise RuntimeError(
            "read-only login failed (Keep) - REFUSING to escalate: "
            "Try/Force/Never/device.connect() would deploy the open "
            "project to the PLC (G-26). Attempts:\n  " +
            "\n  ".join(attempts))
    raise RuntimeError("all login forms failed:\n  " +
                       "\n  ".join(attempts) +
                       "\n" + LOGIN_FAIL_HINT)


def _note_plc(ok, detail, device=None):
    """Record a REAL connection attempt in the PLC-state sidecar.

    Called from _online_connect around the login ladder only: pre-login
    refusals (G-27 uptodate check, confirm gates, missing creds) raise
    BEFORE any login and must not mark the PLC offline - the device was
    never touched. Read by the MCP server's ecockpit_server_status for
    its "plc" section. Best effort: a sidecar failure is logged, never
    raised - it must not mask the command's own result.
    """
    entry = {"online": bool(ok), "ts": time.time(),
             "detail": str(detail)[:200]}
    if device is not None:
        try:
            entry["ip"] = str(device.ip_address)
        except Exception:
            pass
    try:
        tmp = PLC_STATE_FILE + ".tmp"
        with open(tmp, "wb") as fh:
            fh.write(json.dumps(entry).encode("utf-8", "replace"))
        try:
            os.remove(PLC_STATE_FILE)
        except OSError:
            pass
        os.rename(tmp, PLC_STATE_FILE)
    except Exception as exc:
        log("INFO", "plc-state sidecar write failed: {e}".format(e=exc))


def _login_key(project, args):
    """Identity of a login slot: open project + device name."""
    try:
        pp = project.primary_project
        root = str(getattr(pp, "path", "") or getattr(pp, "project_path", ""))
    except Exception:
        root = ""
    return (root, args.get("device", ""))


def _online_connect(project, args, prefer_keep=False, forms=None):
    """Connect to the PLC (login) and return (device, onlineapp).

    Requires PLC_USER/PLC_PASS (.env or env vars). Caller disconnects.
    Logs in via the online-change ladder (_online_login) - NEVER via
    the implicit-download device.connect() unless every explicit form
    fails. Read-only callers pass prefer_keep=True: OnlineChangeOption.
    Keep (stub-only, not in the CHM) logs in WITHOUT applying online
    changes or downloading. WARNING: even a Try login applies online
    changes (code deltas) when the open project differs from the PLC -
    treat deploying commands as potentially deploying.
    forms: whitelist passed to _online_login (e.g. ["Force"] for the
    download-online strict ladder - no fallback, no device.connect()).

    Keep-login protocol (args.keep): a command that logs in leaves the
    session OPEN and caches it in _LOGIN_CACHE; the next keep-command
    reuses it (no ladder at all). Any non-keep online command logs the
    kept session out first so a fresh ladder never collides with a
    live login. Logout: ecockpit_disconnect (clears the cache too).
    """
    key = _login_key(project, args)
    entry = _LOGIN_CACHE.get("entry")
    keep = bool(args.get("keep"))
    if entry is not None:
        if keep and entry.get("key") == key:
            log("INFO", "reusing kept login session (no ladder)")
            _note_plc(True, "reused kept login session", entry["device"])
            return entry["device"], entry["onlineapp"]
        # Stale slot (different project/device) or a deploying command:
        # log the kept session out BEFORE a fresh ladder - a second
        # login while one is live is exactly the modal/wedge scenario.
        log("INFO", "logging out kept session first ({r})".format(
            r="project/device changed" if keep else "non-keep command"))
        _online_disconnect(entry["device"])
        _LOGIN_CACHE.clear()
    if prefer_keep:
        # LIVE 2026-10-22 (G-27): with a pending change, login(Keep) on
        # this build ATTEMPTS A DOWNLOAD anyway (stub semantics not
        # honored), pops a modal error dialog and raises "Login failed".
        # Refuse BEFORE touching the login when out of sync.
        try:
            uptodate = bool(get_application(project).is_uptodate)
        except Exception:
            uptodate = None
        if uptodate is False:
            raise RuntimeError(
                "read-only command REFUSED - the application is NOT "
                "uptodate (a pending change exists) and login(Keep) "
                "would attempt a download in this build (G-27). Sync "
                "first (deploy the change or discard it), then retry.")
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
    onlineapp = online.create_online_application(app)
    try:
        via = _online_login(onlineapp, device, prefer_keep=prefer_keep,
                            engapp=app, forms=forms)
    except Exception as exc:
        _note_plc(False, "{c}: {m}".format(
            c=type(exc).__name__, m=str(exc)[:160]), device)
        raise
    _note_plc(True, "logged in via {v}{k}".format(
        v=via, k="; login KEPT OPEN (session-scoped, args.keep)"
        if keep else ""), device)
    return device, onlineapp


def _online_disconnect(device):
    """Logout. LIVE 2026-10-22 (G-29): a plain disconnect() pops a modal
    when FORCED variables exist ("delete all currently forced variables
    before logging out?") and wedges the relay. forced_disconnect()
    (IScriptOnlineDevice2) cannot hang and keeps the runtime running -
    it is tried FIRST now; plain disconnect stays as the fallback.
    LIVE 2026-10-24: the fallback is INFO, not WARN - the project-tree
    device NEVER carries forced_disconnect (that is an online-device
    method), so the old WARN fired on every login/logout command."""
    if hasattr(device, "forced_disconnect"):
        try:
            device.forced_disconnect()
            log("INFO", "forced_disconnect done (modal-free logout)")
            return
        except Exception as exc:
            log("WARN", "forced_disconnect raised ({e}) - plain disconnect"
                .format(e=exc))
    else:
        log("INFO", "plain disconnect (forced_disconnect not available "
                    "on this device object)")
    try:
        device.disconnect()
        log("INFO", "disconnected from PLC")
    except Exception as exc:
        log("WARN", "disconnect failed: {e}".format(e=exc))


def _display_name(obj):
    """Friendlier name for dynamic ScriptObjects: DIRECT get_name()/.name/
    .Name access, then the repr short-name.

    LIVE 2026-10-22: the old getattr() probe silently never ran on the
    interop wrappers (getattr fails on them, G-01) - the class-name repr
    leaked into snapshot filenames
    (Wago.Eng.Module.Script.ScriptObjects.EScriptProject_3.10.0-pre7.ecp).
    Direct calls work on wrappers and plain objects alike.
    """
    try:
        v = obj.get_name()
        if v:
            return str(v)
    except Exception:
        pass
    for attr in ("name", "Name"):
        try:
            if attr == "name":
                v = obj.name
            else:
                v = obj.Name
            if v:
                return str(v)
        except Exception:
            continue
    return _short_name(obj)


def cmd_status(project):
    """Collect project/application/device info as a plain dict."""
    info = {"project": _display_name(project)}
    # project_sources: the active config triplet + its source (sidecar or
    # .env) + the GUI-open primary - a drift between them is exactly the
    # 'build imported into the wrong project' incident class, so every
    # status makes it visible.
    ps = _project_sources()
    try:
        p = project.path
        ps["open_primary"] = str(p) if p else None
    except Exception:
        ps["open_primary"] = None
    info["project_sources"] = ps
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
    """Read variable(s) online and return their string value(s).

    args.path is one application-relative symbol path, a list of paths,
    or a dict of {path: label}. A multi-read shares ONE login across
    all paths. args.keep (default True for reads): the login stays OPEN
    after the read and is REUSED by the next keep-read - logout only
    via ecockpit_disconnect, or implicitly before the next non-keep
    online command (write/download). Set keep=false to force the old
    login+logout-per-read behavior."""
    raw = args.get("path", "")
    if isinstance(raw, dict):
        paths = list(raw.keys())
    elif isinstance(raw, list):
        paths = list(raw)
    else:
        paths = [raw]
    paths = [p.strip() for p in paths if isinstance(p, str) and p.strip()]
    if not paths:
        raise RuntimeError("read needs args.path, e.g. "
                           "{\"path\": \"GVLName.Variable\"}")
    keep = bool(args.get("keep", True))
    device, onlineapp = _online_connect(project, args, prefer_keep=True)
    try:
        values = {}
        errors = {}
        for p in paths:
            try:  # StandardError on the IronPython relay
                values[p] = str(onlineapp.read_value(p))
                log("OK", "read {p} = {v}".format(p=p, v=values[p]))
            except Exception as exc:
                # LIVE 2026-10-24: the engine raises 'Invalid pointer
                # size.' during symbol resolution for POINTER/REFERENCE
                # TO paths (e.g. stLightEntity.pAutomationCfg) - name
                # the path, or the failure is undiagnosable once the
                # per-command log rotates.
                msg = str(exc)
                hint = ""
                if "Invalid pointer size" in msg:
                    hint = (" - pointer/REFERENCE-typed symbols cannot "
                            "be returned by read_value; read the "
                            "pointee variable or a value-typed member "
                            "instead")
                errors[p] = "{m}{h}".format(m=msg, h=hint)
                log("ERROR", "read_value failed for '{p}': {e}".format(
                    p=p, e=errors[p]))
        if len(paths) == 1:
            if errors:
                raise RuntimeError(
                    "read_value failed for '{p}': {m}".format(
                        p=paths[0], m=errors[paths[0]]))
            result = values[paths[0]]
        else:
            result = json.dumps({"values": values, "errors": errors},
                                indent=2, sort_keys=True, ensure_ascii=False)
        if keep:
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
            _note_plc(True, "login kept open after read "
                            "(session-scoped; logout via disconnect)",
                      device)
        return result
    finally:
        if not keep:
            _online_disconnect(device)


def cmd_download(project, args):
    """Deploy the open project to the PLC. CONFIRM-GATED.

    args: {"mode": "auto"|"online"|"full" (default "auto"),
           "boot": bool (default false - create_boot_application only
                   when asked; writing the boot image on every deploy
                   is wasted flash cycles),
           "confirm": true, "device": ""}.

    mode="online" - PARTIAL deploy (online change) strict: reuses the
    kept login session (no fresh ladder); when no session exists,
    login(OnlineChangeOption.Force) ONLY - online change or refusal,
    never a silent full download. NO device.start() - an online change
    does not stop the application. The session stays OPEN.
    mode="auto" - reuse the kept session like online; when no session
    exists, the classic ladder Try -> Force -> Never -> device.connect().
    device.start() runs ONLY when the deploy went through a FULL form
    (Never / device.connect) - after an online change the application
    is still running. The session stays OPEN.
    mode="full" - always a fresh ladder with login(Never) (+ legacy
    device.connect fallback), replaces the running application
    entirely, then device.start() and logout (today's behavior).

    Returns {"via": <form/session>, "boot": bool, "started": bool,
    "mode": mode}."""
    if not args.get("confirm"):
        raise RuntimeError("download REFUSED - requires args.confirm=true. "
                           "It deploys the open project to the PLC "
                           "(online change or full download per mode).")
    mode = str(args.get("mode", "auto")).lower()
    if mode not in ("auto", "online", "full"):
        raise RuntimeError('download needs args.mode of "auto", "online" '
                           'or "full" (got {m!r})'.format(m=mode))
    boot = bool(args.get("boot"))
    reuse = mode in ("online", "auto")
    key = _login_key(project, args)
    entry = _LOGIN_CACHE.get("entry") if reuse else None
    if entry is not None and entry.get("key") == key:
        device = entry["device"]
        onlineapp = entry["onlineapp"]
        via = "kept session"
        log("INFO", "download on the KEPT session (no ladder)")
    else:
        # Fresh ladder: full uses Never (+ legacy fallback); online
        # uses Force strictly; auto uses the classic Try -> Force ->
        # Never -> connect order. keep=False logs a stale kept session
        # out first (inside _online_connect).
        dargs = dict(args)
        dargs["keep"] = False
        device, onlineapp = _online_connect(
            project, dargs,
            forms=["Force"] if mode == "online" else None)
        via = str(_LOGIN_CACHE.get("last_via", ""))
    try:
        if boot:
            log("INFO", "creating boot application ON DEVICE (updates the "
                "boot image)")
            onlineapp.create_boot_application()
        else:
            log("INFO", "boot skipped (args.boot false) - boot image NOT "
                "updated")
        full_form = ("Never" in via) or ("device.connect()" in via)
        started = False
        if mode == "full" or full_form:
            log("INFO", "starting application on device")
            device.start()
            started = True
        else:
            log("INFO", "start skipped (online change leaves the "
                "application running)")
        log("OK", "download complete via {v} (boot={b}, started={s})"
            .format(v=via, b=boot, s=started))
        if mode != "full":
            # online/auto: the session is synced now - register it for
            # keep-login reuse by reads.
            _LOGIN_CACHE["entry"] = {"key": key, "device": device,
                                     "onlineapp": onlineapp}
            _note_plc(True, "download ({m}) via {v}; session kept open "
                            "(synced)".format(m=mode, v=via), device)
        return {"via": via, "boot": boot, "started": started,
                "mode": mode}
    finally:
        _LOGIN_CACHE.pop("last_via", None)
        if mode == "full":
            _online_disconnect(device)
            _note_plc(False, "logged out after full download", device)


def cmd_disconnect(project, args):
    """Explicit logout from the PLC - G-29 v2 logout ladder.

    Teardown order per active W/F state (probed on the KEPT session,
    no fresh login cycle):
    - FORCED values active: unforce_all_values() first, then a plain
      logout (droga 1); if the unforce fails, forced_disconnect()
      (modal-free, droga 2). A logout with active forces pops the GUI
      modal 'delete all currently forced variables?' otherwise.
    - staged/written values only (regular write): plain logout is safe
      - prepared values are discarded at logout, written ones are kept
      until the logic overwrites them.
    - args.force=true still means 'use forced_disconnect' (manual
      override of the probe result).

    Also drops the kept-login cache so the next read does a fresh
    ladder, and flips the plc state flag offline (server_status)."""
    entry = _LOGIN_CACHE.get("entry")
    _LOGIN_CACHE.clear()
    device = entry["device"] if entry is not None \
        else get_device(project, args.get("device", ""))
    onlineapp = entry.get("onlineapp") if entry is not None else None
    forced = []
    staged = -1
    if onlineapp is not None:
        try:
            forced = [str(e) for e in onlineapp.get_forced_expressions()]
        except Exception as exc:
            log("INFO", "get_forced_expressions probe failed: {e}".format(
                e=exc))
        try:
            staged = len(list(onlineapp.get_prepared_expressions()))
        except Exception:
            pass
    detail = "logged out via disconnect"
    if forced:
        log("INFO", "{n} forced value(s) active - unforce before logout "
            "(G-29 v2)".format(n=len(forced)))
        unforced = False
        try:
            onlineapp.unforce_all_values()
            unforced = True
            log("OK", "unforced {n} value(s) before logout".format(
                n=len(forced)))
        except Exception as exc:
            log("WARN", "unforce_all_values failed: {e} - falling back "
                "to forced_disconnect".format(e=exc))
        if unforced and not args.get("force"):
            _online_disconnect(device)
            detail = ("logged out after unforce ({n} forced)".format(
                n=len(forced)))
            _note_plc(False, detail, device)
            return
        try:
            device.forced_disconnect()
            log("OK", "forced_disconnect done (modal-free logout with "
                "forces)")
            _note_plc(False, "logged out via forced_disconnect ({n} "
                            "forced)".format(n=len(forced)), device)
            return
        except AttributeError:
            log("WARN", "forced_disconnect not available - plain "
                "disconnect")
        except Exception as exc:
            log("WARN", "forced_disconnect raised: {e}".format(e=exc))
    elif staged > 0:
        log("INFO", "{n} staged value(s) discarded at logout (no "
            "forces - plain logout is safe)".format(n=staged))
        detail = "logged out ({n} staged values discarded)".format(
            n=staged)
    if args.get("force"):
        try:
            device.forced_disconnect()
            log("OK", "forced disconnect done")
            _note_plc(False, "logged out via forced disconnect", device)
            return
        except AttributeError:
            log("WARN", "forced_disconnect not available - plain disconnect")
        except Exception as exc:
            log("WARN", "forced_disconnect raised: {e}".format(e=exc))
    _online_disconnect(device)
    _note_plc(False, detail, device)


def cmd_login(project, args):
    """Explicit login with online-change control (login + STAY connected).

    args: {"option": "try"|"force"|"never"|"auto" (default "auto" = the
    ladder order Try -> Force -> Never -> device.connect()),
    "delete_foreign_apps": false, "device": "", "confirm": true}.

    CONFIRM-GATED: a login can apply online changes (code deltas) to the
    RUNNING application. Logout afterwards with ecockpit_disconnect.
    """
    if not args.get("confirm"):
        raise RuntimeError("login REFUSED - requires args.confirm=true. A "
                           "login can apply online changes (code deltas) "
                           "to the RUNNING PLC application.")
    option = str(args.get("option", "auto")).lower()
    delete_foreign_apps = bool(args.get("delete_foreign_apps"))
    user, pw = _require_creds()
    # A fresh explicit ladder invalidates any kept session - drop the
    # cache BEFORE logging in (stale reuse would logout a dead session).
    _LOGIN_CACHE.clear()
    device = get_device(project, args.get("device", ""))
    try:
        aa = project.active_application
        app = aa if aa is not None else get_application(project)
    except Exception:
        app = get_application(project)
    log("INFO", "login to PLC as user '{u}' (option={o}, "
        "delete_foreign_apps={d})".format(
            u=user[0] + "***" if len(user) > 1 else "***",
            o=option, d=delete_foreign_apps))
    online.set_default_credentials(user, pw)
    onlineapp = online.create_online_application(app)

    if option == "auto":
        via = _online_login(onlineapp, device, engapp=app)
        _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                 "device": device, "onlineapp": onlineapp}
        _note_plc(True, "logged in via {v}; session registered for "
                        "keep-login reuse".format(v=via), device)
        log("OK", "login complete (auto ladder) - session stays open; "
            "logout with ecockpit_disconnect")
        return {"logged_in": True, "via": "auto", "stays_connected": True}

    members = {"try": "Try", "force": "Force", "never": "Never"}
    if option not in members or OnlineChangeOption is None:
        raise RuntimeError('login needs args.option of "try", "force", '
                           '"never" or "auto"{suffix}'.format(
                               suffix="" if OnlineChangeOption is not None
                               else " (OnlineChangeOption is unavailable "
                                    "outside e!COCKPIT)"))
    try:
        if option == "try":
            change_option = OnlineChangeOption.Try
        elif option == "force":
            change_option = OnlineChangeOption.Force
        else:
            change_option = OnlineChangeOption.Never
    except Exception as exc:
        raise RuntimeError("OnlineChangeOption.{m} unavailable: "
                           "{e}".format(m=members[option], e=exc))
    try:
        onlineapp.login(change_option, delete_foreign_apps)
    except Exception as exc:
        recovered = _login_gap_timeout_recovery(
            onlineapp, device, app, change_option,
            "login(OnlineChangeOption.{m})".format(m=members[option]),
            exc, delete_foreign_apps=delete_foreign_apps)
        if recovered == "applied":
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
            log("OK", "login complete ({o}) - the change was applied "
                "despite the task-gap timeout; session stays open"
                .format(o=option))
            return {"logged_in": True, "via": option,
                    "stays_connected": True,
                    "note": "task-gap timeout; runtime applied the change"}
        if recovered == "retry-ok":
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
            log("OK", "login complete ({o}) on the retry after the "
                "task-gap timeout; session stays open".format(o=option))
            return {"logged_in": True, "via": option,
                    "stays_connected": True,
                    "note": "succeeded on retry after task-gap timeout"}
        raise RuntimeError(
            "login({o}) failed: {c}: {m}".format(
                o=members[option], c=type(exc).__name__,
                m=str(exc)[:200]) +
            "\n" + LOGIN_FAIL_HINT)
    _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                             "device": device, "onlineapp": onlineapp}
    _note_plc(True, "logged in via OnlineChangeOption.{m}; session "
                    "registered for keep-login reuse".format(
                        m=members[option]), device)
    log("OK", "login complete (OnlineChangeOption.{m}) - session stays "
        "open; logout with ecockpit_disconnect".format(m=members[option]))
    return {"logged_in": True, "via": members[option],
            "delete_foreign_apps": delete_foreign_apps,
            "stays_connected": True}


def cmd_source_download(project, args):
    """Push the SOURCE project to the PLC (device.download_source).

    CONFIRM-GATED - it writes to the PLC. Deliberately not the same as
    ecockpit_download (which deploys the compiled application): this
    ships the source archive so the PLC carries its own sources.
    Saves the open project FIRST (best-effort, same as cmd_snapshot):
    live 2026-10-21 - with unsaved changes the engine NRE'd; after the
    user saved (GUI prompt) the same operation uploaded fine, so we
    save by default.
    The source push lives on the ScriptOnlineDevice (created via
    online.create_online_device) as download_source(bCompact,
    additional_items) - ScriptLib stubs; the online APPLICATION's
    source_download() NREs (wrong object, live 2026-10-21).
    """
    if not args.get("confirm"):
        raise RuntimeError("source_download REFUSED - requires "
                           "args.confirm=true (it writes the source "
                           "project to the PLC)")
    log("INFO", "saving the open project first (source_download needs a "
                "saved project)")
    try:
        project.save()
        log("OK", "project saved")
    except Exception as exc:
        # LIVE 2026-10-21: "Current project is stored with a temporary
        # file-name" = the project was never saved as a .ecp. Proceeding
        # is still correct when a human can answer the engine's save
        # prompt in the GUI (that is exactly the flow that uploaded
        # fine); unattended runs will NRE instead.
        log("WARN", "pre-save failed (continuing - if e!COCKPIT shows a "
            "save dialog, answer it there; unattended runs will likely "
            "fail): {e}".format(e=exc))
    user, pw = _require_creds()
    online.set_default_credentials(user, pw)
    device_obj = get_device(project, args.get("device", ""))
    odev = _create_online_device(project, args)
    try:
        try:
            odev.connect()
            log("INFO", "online device connected")
        except Exception as exc:
            log("WARN", "online device connect failed (download_source "
                "may still work): {e}".format(e=exc))
        errors = []
        try:
            odev.download_source(False, None)
            log("OK", "source project written to the PLC "
                "(download_source)")
            return {"source_download": True, "via": "download_source"}
        except Exception as exc:
            errors.append("download_source: {c}: {m}".format(
                c=type(exc).__name__, m=str(exc)[:200]))
            log("WARN", "download_source failed: {e}".format(
                e=str(exc)[:200]))
        # Fallback: the online APPLICATION's source_download (NRE'd live
        # 2026-10-21 - kept as a last rung for other builds).
        onlineapp = online.create_online_application(
            get_application(project))
        try:
            onlineapp.source_download()
            log("OK", "source project written to the PLC "
                "(source_download fallback)")
            return {"source_download": True, "via": "source_download"}
        except Exception as exc:
            errors.append("source_download: {c}: {m}".format(
                c=type(exc).__name__, m=str(exc)[:200]))
        raise RuntimeError("source push failed:\n  " +
                           "\n  ".join(errors))
    finally:
        try:
            odev.Dispose()
        except Exception:
            try:
                odev.disconnect()
            except Exception:
                pass


def cmd_prepared_values(project, args):
    """Read back staged (prepared) and forced values from the online app.

    args: {"expression": optional single variable} - with it, that
    expression's staged value via get_prepared_value(expression);
    without, every staged expression via get_prepared_expressions.
    Also lists currently forced variables (get_forced_expressions).
    Read-only - never touches the PLC state beyond the login.
    """
    device, onlineapp = _online_connect(project, args, prefer_keep=True)
    try:
        expression = str(args.get("expression", "")).strip()

        def _staged_value(expr):
            try:
                return {"expression": str(expr),
                        "value": str(onlineapp.get_prepared_value(expr))}
            except Exception as exc:
                return {"expression": str(expr),
                        "error": str(exc)[:150]}

        staged = []
        if expression:
            staged.append(_staged_value(expression))
        else:
            try:
                exprs = list(onlineapp.get_prepared_expressions())
            except Exception as exc:
                exprs = []
                log("WARN", "get_prepared_expressions failed: "
                    "{e}".format(e=exc))
            for expr in exprs:
                staged.append(_staged_value(expr))
        forced = []
        try:
            forced = [str(e) for e in onlineapp.get_forced_expressions()]
        except Exception as exc:
            log("WARN", "get_forced_expressions failed: {e}".format(e=exc))
        log("OK", "prepared values: {s} staged, {f} forced".format(
            s=len(staged), f=len(forced)))
        if bool(args.get("keep", True)):
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
        return {"staged": staged, "forced": forced}
    finally:
        if not bool(args.get("keep", True)):
            _online_disconnect(device)


def cmd_write(project, args):
    """Write (or force) PLC variable values via prepared values.

    args: {"values": {expr: iec_literal}, "force": bool, "confirm": true}.
    force=true uses force_prepared_values (values stay forced), otherwise
    write_prepared_values (one-shot write). Requires confirm - this changes
    values on the RUNNING application.
    """
    if not args.get("confirm"):
        raise RuntimeError("write REFUSED - requires args.confirm=true. It "
                           "changes variable values on the RUNNING PLC "
                           "application.")
    values = args.get("values") or {}
    if not isinstance(values, dict) or not values:
        raise RuntimeError('write needs args.values, e.g. '
                           '{"values": {"GVLName.bFlag": "TRUE"}}')
    keep = bool(args.get("keep", True))
    device, onlineapp = _online_connect(project, args, prefer_keep=True)
    try:
        for expr in sorted(values):
            onlineapp.set_prepared_value(expr, str(values[expr]))
            log("INFO", "prepared {p} := {v}".format(p=expr, v=values[expr]))
        if args.get("force"):
            onlineapp.force_prepared_values()
            log("OK", "forced {n} value(s) on the device".format(
                n=len(values)))
        else:
            onlineapp.write_prepared_values()
            log("OK", "wrote {n} value(s) to the device".format(
                n=len(values)))
        readback = {}
        for expr in sorted(values):
            try:
                readback[expr] = str(onlineapp.read_value(expr))
            except Exception:
                readback[expr] = "<read back failed>"
        if keep:
            # Logout with ACTIVE FORCES pops the G-29 GUI modal - the
            # session STAYS OPEN (logout = ecockpit_disconnect, which
            # unfources first). Staged/written-only values are safe.
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
            _note_plc(True, "{k} applied; session kept open "
                            "(logout via disconnect)".format(
                                k="forces" if args.get("force")
                                else "writes"), device)
        return readback
    finally:
        if not keep:
            _online_disconnect(device)


def cmd_unforce(project, args):
    """Release all forced values on the PLC. Reuses the kept login
    session; the session stays open afterwards (keep=false restores the
    old login+logout ladder)."""
    keep = bool(args.get("keep", True))
    device, onlineapp = _online_connect(project, args, prefer_keep=True)
    try:
        onlineapp.unforce_all_values()
        log("OK", "unforced all values")
        if keep:
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
            _note_plc(True, "unforced all; session kept open", device)
        return "unforced all"
    finally:
        if not keep:
            _online_disconnect(device)


def cmd_stop(project, args):
    """Stop the running application on the PLC. Requires confirm.

    Reuses the kept login session when one exists (no fresh ladder);
    keeps the session open afterwards (keep=false restores login+
    logout). Stop leaves no W/F, so a later logout is modal-free."""
    if not args.get("confirm"):
        raise RuntimeError("stop REFUSED - requires args.confirm=true. It "
                           "STOPS the running PLC application.")
    keep = bool(args.get("keep", True))
    device, onlineapp = _online_connect(project, args, prefer_keep=True)
    try:
        stopped_via = ""
        try:
            onlineapp.stop()
            stopped_via = "onlineapp.stop"
        except AttributeError:
            device.stop()
            stopped_via = "device.stop"
        log("OK", "application stopped (via {v})".format(v=stopped_via))
        state = {}
        try:
            state["operation_state"] = str(onlineapp.operation_state)
        except Exception:
            pass
        state["stopped_via"] = stopped_via
        if keep:
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
            _note_plc(True, "application stopped; session kept open",
                      device)
        return state
    finally:
        if not keep:
            _online_disconnect(device)


def cmd_state(project, args):
    """Online state readout: login/app/operation state + device info.

    Direct-syntax attribute access throughout - getattr() fails on these
    .NET interop objects even for members that work via direct access.
    Reuses the kept login session and keeps it open (keep=false restores
    the old login+logout ladder) - the poller pays no ladder per call.
    """
    keep = bool(args.get("keep", True))
    device, onlineapp = _online_connect(project, args, prefer_keep=True)
    try:
        info = {}
        try:
            info["is_logged_in"] = str(onlineapp.is_logged_in)
        except Exception:
            pass
        try:
            info["application_state"] = str(onlineapp.application_state)
        except Exception:
            pass
        try:
            info["operation_state"] = str(onlineapp.operation_state)
        except Exception:
            pass
        try:
            info["device_connected"] = str(device.connected)
        except Exception:
            pass
        try:
            info["device"] = _display_name(device)
        except Exception:
            pass
        log("OK", "state: {j}".format(j=json.dumps(info)))
        if keep:
            _LOGIN_CACHE["entry"] = {"key": _login_key(project, args),
                                     "device": device,
                                     "onlineapp": onlineapp}
        return info
    finally:
        if not keep:
            _online_disconnect(device)


def _export_target_path(out_dir, app_name):
    """File target for export_xml.

    The CHM's `path` parameter is "The path of the FILE we export into" -
    NEVER a directory (live 2026-10-21: passing a directory makes .NET
    try to open it as a file, failing with "Access to the path is
    denied" on any directory, existing or not).
    """
    name = app_name or "application"
    if name.startswith("Name="):
        name = name[len("Name="):]
    base = re.sub(r"[^A-Za-z0-9_.-]+", "_", name.strip()) or "application"
    return os.path.join(out_dir, base + ".xml")


def cmd_export(project, args, summary):
    """Export the application objects to PLCopen XML.

    args: {"dir": target directory} (default
    <project root>/plcopensrc-export). Exports TWO files:
    <dir>/<application>.xml (the whole application) and
    <dir>/<application>.Library Manager.xml (the application-scope
    Library Manager - owner-directed LM reconciliation 2026-10-21: the
    LM XML must reach plcopensrc/ like the POUs do, it carries the
    library-reference state the application compiles against).
    export_xml's `path` is a FILE path (see _export_target_path).
    Ladder per file: 4-arg call, 5-arg IScriptProject2 retry on
    TypeError, then the string-return form (path=None -> the CHM
    guarantees the whole XML as the return value) written by us.
    Reporter errors/warnings count into the command summary (import
    parity). The reporter must subclass the REAL injected interface -
    exposed as `ExportReporter`, NOT `IExportReporter` (see
    _export_reporter_base).
    """
    out_dir = args.get("dir") or ""
    if not out_dir and PROJECT_ROOT:
        out_dir = os.path.join(PROJECT_ROOT, "plcopensrc-export")
    if not out_dir:
        raise RuntimeError("export needs args.dir (or ECP_PROJECT_ROOT for "
                           "the default location)")
    if not os.path.isdir(out_dir):
        os.makedirs(out_dir)
    app = get_application(project)
    reporter = make_export_reporter(summary)

    def _export_one(objs, target):
        """One export_xml ladder. Returns the mode string."""
        try:
            try:
                project.export_xml(reporter, objs, target, True)
            except TypeError:
                log("INFO", "retrying export_xml with folder-structure arg "
                            "(IScriptProject2 overload)")
                project.export_xml(reporter, objs, target, True, True)
            if not os.path.isfile(target):
                raise RuntimeError("export_xml reported success but wrote no "
                                   "file: {t}".format(t=target))
        except Exception as exc:
            log("INFO", "file-based export failed ({c}: {m}) - falling back "
                        "to the string-return form".format(
                            c=type(exc).__name__, m=str(exc)[:160]))
            xml = project.export_xml(reporter, objs, None, True)
            with open(target, "wb") as fh:
                fh.write(_to_bytes(xml))
            return "string-fallback"
        return "file"

    jobs = [([app], _export_target_path(out_dir, _short_name(app)))]
    try:
        lms = app.find("Library Manager", True)
    except Exception:
        lms = None
    n_lm = 0
    if lms is not None:
        try:
            n_lm = lms.Count
        except Exception:
            n_lm = len(lms) if isinstance(lms, (list, tuple)) else 0
    for i in range(n_lm):
        try:
            lm = lms[i]
            lm_name = "{}.{}".format(_short_name(app), _short_name(lm))
            jobs.append(([lm], _export_target_path(out_dir, lm_name)))
        except Exception:
            continue

    modes = []
    for objs, target in jobs:
        modes.append(_export_one(objs, target))

    target, mode = jobs[0][1], modes[0]
    files = sorted(glob.glob(os.path.join(out_dir, "*.xml")))
    size = os.path.getsize(target) if os.path.isfile(target) else None
    log("OK", "export wrote {n} file(s) ({b} bytes for the first, modes "
              "{m}, {e} error(s))".format(
        n=len(jobs), b=size, m=modes, e=summary["errors"]))
    return {"dir": out_dir, "file": target, "mode": mode, "bytes": size,
            "xml_files": len(files), "first_files": files[:10]}


def _plcfs_reflect_members(odev, keyword):
    """CLR reflection: the wrapper's REAL method names containing keyword
    (G-02: reflection is the only reliable discovery on the interop
    wrappers - the stubs lie in both directions, G-13)."""
    try:
        import clr
        t = clr.GetClrType(odev)
        names = sorted(set(m.Name for m in t.GetMethods()))
    except Exception:
        return []
    return [n for n in names if keyword.lower() in n.lower()]


def _plcfs_member(holder, name):
    """Fetch one plcfs member by literal name.

    getattr() is unreliable on the interop wrappers (false negatives per
    the gotcha at the top of this file), so every candidate gets its own
    direct-access branch. Raises AttributeError when the member is not
    present.
    """
    if name == "get_file_list_of_directory":
        return holder.get_file_list_of_directory
    if name == "get_files_of_directory":
        return holder.get_files_of_directory
    if name == "get_file_list":
        return holder.get_file_list
    if name == "get_directory_listing":
        return holder.get_directory_listing
    if name == "list_files":
        return holder.list_files
    if name == "get_files":
        return holder.get_files
    if name == "download_file":
        return holder.download_file
    if name == "upload_file":
        return holder.upload_file
    if name == "delete_file":
        return holder.delete_file
    # ScriptOnlineDevice extras (stubs 3.5.9.0) - LIVE 2026-10-22: these
    # branches were MISSING, so mkdir/rename/rmdir always failed with
    # "no member" even though the wrapper has them
    if name == "create_directory":
        return holder.create_directory
    if name == "rename_file":
        return holder.rename_file
    if name == "rename_directory":
        return holder.rename_directory
    if name == "delete_directory":
        return holder.delete_directory
    raise AttributeError(name)


_LS_CANDIDATES = ("get_file_list_of_directory", "get_files_of_directory",
                  "get_file_list", "get_directory_listing", "list_files",
                  "get_files")
_FILE_CANDIDATES = {"get": ("download_file",), "put": ("upload_file",),
                    "rm": ("delete_file",)}
# ScriptOnlineDevice extras (ScriptLib stubs, 3.5.9.0): rename_file,
# create_directory, rename_directory, delete_directory(remote, recursive)
_MANIP_OPS = ("rename", "mkdir", "rmdir")


def _create_online_device(project, args):
    """ScriptOnlineDevice via online.create_online_device - the holder
    of the PLC file API + source archive (ScriptLib stubs). Ladder: the
    no-arg form (per stubs: the engine then uses the device of the
    active application), then the device's ScriptObject via
    project.find (the raw interop device from project.devices is
    REJECTED - "expected IExtendedObject[IScriptObject]"). Callers
    must Dispose() the result.

    LIVE 2026-10-22 (G-26): CONNECTING this device performs a
    login-WITH-DOWNLOAD of the open application - it only looks safe
    when the application is uptodate (no delta to deploy). Non-uptodate
    therefore REFUSES without args.confirm.
    """
    try:
        uptodate = bool(get_application(project).is_uptodate)
    except Exception:
        uptodate = None
    if uptodate is False and not args.get("confirm"):
        raise RuntimeError(
            "create_online_device REFUSED - connecting the online "
            "device performs a login-WITH-DOWNLOAD of the open "
            "application and the application is NOT uptodate "
            "(is_uptodate=false), so the connect would deploy it. "
            "Bring the project and PLC in sync or pass args.confirm"
            "=true explicitly (G-26).")
    try:
        odev = online.create_online_device()
        log("INFO", "created online device (active application's device)")
        return odev
    except Exception as exc:
        attempts = ["no-arg: {e}".format(e=str(exc)[:140])]
    device = get_device(project, args.get("device", ""))
    name = _short_name(device)
    if name.startswith("Name="):
        name = name[len("Name="):].strip() or "Device"
    found = project.find(name, True)
    if not found:
        raise RuntimeError("create_online_device failed: " +
                           "; ".join(attempts) +
                           " - and project.find found no device named "
                           "'{n}'".format(n=name))
    try:
        odev = online.create_online_device(found[0])
        log("INFO", "created online device (device ScriptObject)")
        return odev
    except Exception as exc:
        raise RuntimeError("create_online_device failed in both forms: " +
                           "; ".join(attempts) + "; script-object form: "
                           "{e}".format(e=str(exc)[:160]))


def cmd_plcfs(project, args):
    """File operations on the PLC's file system (IScriptOnlineDevice4).

    args: {"op": "ls"|"get"|"put"|"rm", "remote": ..., "local": ...,
           "device": ..., "confirm": true for put/rm}.
    NOTE the argument order quirk from ScriptEngine.chm:
    download_file(local, remote, force) - PLC -> host;
    upload_file(remote, local, force)   - host -> PLC.
    """
    op = args.get("op", "")
    if op not in ("ls", "get", "put", "rm") + _MANIP_OPS:
        raise RuntimeError('plcfs needs args.op of "ls", "get", "put", '
                           '"rm", "rename", "mkdir" or "rmdir"')
    if op in ("put", "rm", "rename", "mkdir", "rmdir") and \
            not args.get("confirm"):
        raise RuntimeError("plcfs {o} REFUSED - requires args.confirm=true "
                           "(it modifies files on the PLC)".format(o=op))
    user, pw = _require_creds()
    online.set_default_credentials(user, pw)
    device_obj = get_device(project, args.get("device", ""))
    # LIVE 2026-10-21 (ScriptLib stubs + reflection): the PLC file API
    # lives on the ScriptOnlineDevice created via
    # online.create_online_device() - NOT on the project-tree device
    # object (EScriptDevice: 32 members, none file-related).
    odev = _create_online_device(project, args)
    conn_state = [False]

    def _ensure_connected():
        try:
            if bool(odev.connected):
                return
        except Exception:
            pass
        try:
            odev.connect()
            conn_state[0] = True
            log("INFO", "online device connected")
        except Exception as exc:
            log("WARN", "online device connect failed (ops may still "
                "work): {e}".format(e=exc))

    def _dispose():
        try:
            odev.Dispose()
        except Exception:
            try:
                odev.disconnect()
            except Exception:
                pass

    try:
        if op == "ls":
            remote_dir = args.get("remote", "/")
            # LIVE 2026-10-22: the connected device refused "/" with
            # "Get directory entries failed" - PLC layouts vary, so on
            # failure we probe well-known directories and report which
            # one listed.
            dir_candidates = [remote_dir]
            for candidate in ("/", "/tmp", "/PlcLogic", "."):
                if candidate not in dir_candidates:
                    dir_candidates.append(candidate)
            last_errors = []
            for name in _LS_CANDIDATES:
                try:
                    fn = _plcfs_member(odev, name)
                except AttributeError:
                    continue
                _ensure_connected()
                for attempt_dir in dir_candidates:
                    try:
                        infos = fn(attempt_dir)
                    except TypeError as exc:
                        # member exists but rejected (dir) - keep it in
                        # the log, try the next candidate
                        log("INFO", "plcfs: odev.{n} rejected the "
                            "arguments: {e}".format(n=name, e=exc))
                        last_errors.append("odev.{n}: {e}".format(
                            n=name, e=str(exc)[:120]))
                        break
                    except Exception as exc:
                        log("INFO", "plcfs: odev.{n}({d}) failed: "
                            "{e}".format(n=name, d=attempt_dir,
                                         e=str(exc)[:120]))
                        last_errors.append("odev.{n}({d}): {e}".format(
                            n=name, d=attempt_dir, e=str(exc)[:120]))
                        continue
                    out = []
                    for fi in infos:
                        entry = {"name": ""}
                        for attr in ("name", "is_directory", "is_file",
                                     "size", "last_modification_time"):
                            try:
                                if attr == "name":
                                    entry["name"] = str(fi.name)
                                elif attr == "is_directory":
                                    entry["is_directory"] = \
                                        bool(fi.is_directory)
                                elif attr == "is_file":
                                    entry["is_file"] = bool(fi.is_file)
                                elif attr == "size":
                                    entry["size"] = str(fi.size)
                                elif attr == "last_modification_time":
                                    entry["last_modification_time"] = \
                                        str(fi.last_modification_time)
                            except Exception:
                                continue
                        out.append(entry)
                log("OK", "plcfs ls {d} via create_online_device.{n}: "
                    "{c} entries".format(d=attempt_dir, n=name, c=len(out)))
                return {"dir": attempt_dir, "requested_dir": remote_dir,
                        "via": "create_online_device.{n}".format(n=name),
                        "entries": out}
            raise RuntimeError(
                "no file-listing member worked on the ScriptOnlineDevice "
                "(created via online.create_online_device; stubs confirm "
                "get_file_list_of_directory on it - if you see this, the "
                "runtime object differs from the stub). Probed: "
                "{c}.".format(c=", ".join(_LS_CANDIDATES)))
        if op in ("get", "put", "rm"):
            remote = args.get("remote", "")
            local = args.get("local", "")
            if op in ("get", "put") and (not remote or not local):
                raise RuntimeError("plcfs {o} needs args.remote (PLC path) "
                                   "and args.local (host path)".format(o=op))
            if op == "rm" and not remote:
                raise RuntimeError("plcfs rm needs args.remote (PLC path)")
            fn = None
            for name in _FILE_CANDIDATES[op]:
                try:
                    fn = (name, _plcfs_member(odev, name))
                    break
                except AttributeError:
                    continue
            if fn is None:
                raise RuntimeError(
                    "no {o} member on the ScriptOnlineDevice (probed "
                    "{c})".format(o=_FILE_CANDIDATES[op][0],
                                  c=", ".join(_FILE_CANDIDATES[op])))
            member_name, member = fn
            _ensure_connected()
            if op == "get":
                d = os.path.dirname(local)
                if d and not os.path.isdir(d):
                    os.makedirs(d)
                # LIVE 2026-10-22 (G-13): this build's download_file is
                # (remote_file, local_file) - REMOTE FIRST, opposite of
                # the stub; (local, remote) made it look for the local
                # path as the source ("Could not find file")
                member(remote, local, True)
                log("OK", "plcfs get {r} -> {l} via {n}".format(
                    r=remote, l=local, n=member_name))
                return {"remote": remote, "local": local,
                        "bytes": os.path.getsize(local) if
                        os.path.isfile(local) else None}
            if op == "put":
                if not os.path.isfile(local):
                    raise RuntimeError("local file not found: {l}".format(
                        l=local))
                # force defaults True; force=False on an EXISTING remote
                # file is the overwrite-prompt trigger (prompt_answers
                # end-to-end test, G-07-style: one clean attempt only)
                force = bool(args.get("force", True))
                # LIVE 2026-10-22 (G-13): upload is (local_file,
                # remote_file) on this build - LOCAL FIRST (mirror of the
                # download order); the stub's (remote, local) order made
                # the engine treat the remote path as a local file
                try:
                    member(local, remote, force)
                    form = "3-arg (local, remote, force)"
                except TypeError:
                    member(local, remote)
                    form = "2-arg (local, remote)"
                log("OK", "plcfs put {l} -> {r} (force={f}, {fm}) via {n}"
                    .format(l=local, r=remote, f=force, fm=form,
                            n=member_name))
                return {"local": local, "remote": remote, "force": force,
                        "form": form}
            member(remote)
            log("OK", "plcfs rm {r} via {n}".format(r=remote, n=member_name))
            return {"deleted": remote}
        if op in _MANIP_OPS:
            remote = args.get("remote", "")
            new_name = str(args.get("new_name", "")).strip()
            if op == "rename" and (not remote or not new_name):
                raise RuntimeError("plcfs rename needs args.remote and "
                                   "args.new_name")
            if op == "mkdir" and not remote:
                raise RuntimeError("plcfs mkdir needs args.remote (new "
                                   "directory path)")
            if op == "rmdir" and not remote:
                raise RuntimeError("plcfs rmdir needs args.remote "
                                   "(directory path)")
            members = {"rename": ("rename_file", "rename_directory"),
                       "mkdir": ("create_directory",),
                       "rmdir": ("delete_directory",)}
            reflected = _plcfs_reflect_members(odev, "")
            log("INFO", "plcfs {o}: wrapper methods (CLR reflection): "
                "{r}".format(o=op, r=reflected or "NONE"))
            for name in members[op]:
                try:
                    member = _plcfs_member(odev, name)
                except AttributeError:
                    continue
                _ensure_connected()
                if op == "rename":
                    member(remote, new_name)
                elif op == "mkdir":
                    member(remote)
                else:
                    member(remote, bool(args.get("recursive", False)))
                log("OK", "plcfs {o} {r} via {n}".format(
                    o=op, r=remote, n=name))
                return {"op": op, "remote": remote,
                        "new_name": new_name or None, "via": name}
            raise RuntimeError(
                "no {o} member on the ScriptOnlineDevice (probed "
                "{c})".format(o=op, c=", ".join(members[op])))
    finally:
        _dispose()


def cmd_upload_source(project, args):
    """Upload (read back) the source project archive from the PLC.

    args: {"path": host archive path} (default <work dir>/
    plc_source_upload.projectarchive). Lives on the ScriptOnlineDevice
    (online.create_online_device) - the CHM's IScriptOnlineDevice3
    section; the project-tree device object never had it.
    """
    path = args.get("path", "")
    if not path:
        path = os.path.join(WORK_DIR, "plc_source_upload.projectarchive")
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    user, pw = _require_creds()
    online.set_default_credentials(user, pw)
    device_obj = get_device(project, args.get("device", ""))
    odev = _create_online_device(project, args)
    try:
        try:
            odev.connect()
            log("INFO", "online device connected")
        except Exception as exc:
            log("WARN", "online device connect failed (upload_source may "
                "still work): {e}".format(e=exc))
        odev.upload_source(path)
        log("OK", "source archive written to {p}".format(p=path))
        return {"path": path, "bytes": os.path.getsize(path) if
                os.path.isfile(path) else None}
    finally:
        try:
            odev.Dispose()
        except Exception:
            try:
                odev.disconnect()
            except Exception:
                pass


def cmd_save_archive(project, args):
    """Save the project as a .projectarchive (single-file backup).

    args: {"path": host path} (default <snapshots>/<project>_<ts>
    .projectarchive). IScriptProject(.9).save_archive. LIVE 2026-10-21:
    the plain 1-arg form dies with "Object reference not set" (NRE deep
    in the engine, nothing documented in the CHM) - most plausibly the
    IronPython binding of the params-array overload
    save_archive(String, params IScriptProjectArchiveCategory[]). We
    probe the documented forms in order and keep the first that writes
    the file:
      1. save_archive(path)
      2. save_archive(path, None)
      3. save_archive(path, None, None, None)   (IScriptProject9 form;
         per CHM, None additional_categories = the default selection)
      4. save_archive(path, None, None, ArchiveCategories.default)
         (live 2026-10-21: forms 1+2 NRE and form 3 is rejected with
         "Multiple targets could match" - but the typed category alone
         still leaves two overloads matching on the None slots)
      5/6. save_archive(path, "", ArchiveCategories.default|none)
         (comment="" + ONE typed category pins the
          (String, String, params IScriptProjectArchiveCategory[])
          overload - the only unambiguous shape)
    If every form fails, the error lists all attempts and points at
    ecockpit_snapshot (plain file copy - works live) as the backup
    alternative.
    """
    path = args.get("path", "")
    if not path:
        if not SNAPSHOTS_DIR:
            raise RuntimeError("save_archive needs args.path (or "
                               "ECP_PROJECT_ROOT/ECP_SNAPSHOTS_DIR for the "
                               "default location)")
        if not os.path.isdir(SNAPSHOTS_DIR):
            os.makedirs(SNAPSHOTS_DIR)
        path = os.path.join(
            SNAPSHOTS_DIR, "{p}_{ts}.projectarchive".format(
                p=_snapshot_prefix(project),
                ts=datetime.datetime.now().strftime("%Y%m%d-%H%M%S")))
    d = os.path.dirname(path)
    if d and not os.path.isdir(d):
        os.makedirs(d)

    forms = [
        (1, (path,), {}),
        (2, (path, None), {}),
        (3, (path, None, None, None), {}),
    ]
    if ArchiveCategories is not None:
        # direct member access only - getattr() fails on the interop
        # objects; some categories may throw in customized environments
        # (per CHM)
        for prop in ("default", "none"):
            try:
                if prop == "default":
                    category = ArchiveCategories.default
                else:
                    category = ArchiveCategories.none
            except Exception as exc:
                log("INFO", "ArchiveCategories.{p} unavailable: "
                    "{e}".format(p=prop, e=exc))
                continue
            forms.append((len(forms) + 1, (path, "", category), {}))
    # NAMED-ARGS rung (post-save-retest): positional forms NRE'd in all
    # shapes even with a saved, named project - the named IScriptProject9
    # form may bind the overload positional calls cannot disambiguate.
    forms.append((len(forms) + 1, (), {
        "path": path, "comment": None, "additional_files": None,
        "additional_categories": None}))
    attempts = []
    for number, argv, kwargs in forms:
        log("INFO", "save_archive: trying form {n} "
            "save_archive(path{dots})".format(
                n=number, dots=", None" * (number - 1)))
        try:
            project.save_archive(*argv, **kwargs)
        except Exception as exc:
            attempts.append("form {n}: {c}: {m}".format(
                n=number, c=type(exc).__name__, m=str(exc)[:200]))
            log("WARN", "save_archive form {n} failed: {c}: {m}".format(
                n=number, c=type(exc).__name__, m=str(exc)[:200]))
            continue
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            log("OK", "project archive written to {p} (form {n})".format(
                p=path, n=number))
            return {"path": path, "form": number,
                    "bytes": os.path.getsize(path)}
        attempts.append("form {n}: returned but wrote no file".format(
            n=number))
        log("WARN", "save_archive form {n} returned but wrote no "
            "file".format(n=number))
    raise RuntimeError(
        "save_archive failed in all {n} documented forms:\n  {a}\n"
        "alternative: ecockpit_snapshot (plain .ecp file copy) for a "
        "backup that does not use save_archive".format(
            n=len(forms), a="\n  ".join(attempts)))


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


def _strip_trailing_version(base):
    """Drop a trailing version token from a project name so it is not
    duplicated in snapshot files: the project FILE often carries the
    version (s4-v3.10.0.ecp) and cmd_snapshot appends _<version> again.
    s4-v3.10.0 / s4-v3.10.0-pre / MyApp_1.2.3 -> s4 / s4 / MyApp.
    Requires a separator (or the whole name) so 'chapter2.5' survives."""
    return re.sub(r"(?:^|[-_ ])v?\d+(?:\.\d+)+(?:-[0-9A-Za-z.]+)*$", "", base)


def _snapshot_prefix(project):
    """Filename-safe SHORT project name for snapshot files.

    Preference: the on-disk project FILE base (what the user actually
    calls the project), then the display name, then 'project'.
    LIVE 2026-10-22: EScriptProject exposes neither get_name() nor
    .name/.Name (unlike device objects), and the GUI autosave path is a
    GUID blob - so a non-autosave project.path wins, everything else
    falls through.
    LIVE 2026-10-23: a trailing version on the project file is stripped
    (s4-v3.10.0.ecp -> prefix 's4') so the snapshot reads s4_3.10.0.ecp
    instead of s4-v3.10.0_3.10.0.ecp."""
    try:
        p = project.path
        p = str(p) if p else ""
        if p and os.path.isfile(p) and "Autosave" not in p                 and not p.endswith(".autosave"):
            base = os.path.splitext(os.path.basename(p))[0]
            safe = re.sub(r"[^A-Za-z0-9_.-]+", "_",
                          _strip_trailing_version(base)).strip("_")
            if safe:
                return safe
    except Exception:
        pass
    try:
        name = _display_name(project)
    except Exception:
        name = ""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_",
                  _strip_trailing_version(name or "")).strip("_")
    return safe or "project"


def _on_disk_project_path(project):
    """The on-disk .ecp to read/copy: the open project's path
    (IScriptProject.path - even the GUI autosave, which IS the current
    state), then the explicit/discovered project, then the projects-dir
    search. Returns '' when nothing exists."""
    try:
        p = project.path
        if p and os.path.isfile(str(p)):
            return str(p)
    except Exception:
        pass
    src = PROJECT_FILE or discover_project_file()
    if src and os.path.isfile(src):
        return src
    src = _find_open_source_ecp()
    if src and os.path.isfile(src):
        return src
    return ""


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
    src = _on_disk_project_path(project)
    if src:
        log("INFO", "on-disk project: {p}".format(p=src))
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
# Library manager (global `librarymanager` - ILibManager, ScriptEngine.chm)
# --------------------------------------------------------------------------
# Curation of compiled-library repositories from script. There is NO
# project/application-level Library Manager API in this build: the injected
# `librarymanager` global is the whole surface. Per the CHM:
#   find_library(display_name)       -> list of (IManagedLib, ILibRepository)
#   get_library(name, repo=None)     -> IManagedLib (repo None: repos in order)
#   get_all_libraries(exclude_shadowed=True | repository) -> [IManagedLib]
#   install_library(filepath, repository=None, overwrite=False) -> IManagedLib
#   uninstall_library(repository, library)
#   repositories -> [ILibRepository]  (name / root_folder / editable;
#                                      order == search order, snapshot!)
#   insert_repository(rootfolder, name, index=-1)   (rootfolder must EXIST)
#   remove_repository(repository, delete_on_disk=False)
#   update_repository(repository, new_name=None, new_location=None,
#                     copy_libraries=False)
#   move_repository(repository, new_index)          (-1 = end of list)
# LIVE-VERIFIED 2026-10-21 (WAGO e!COCKPIT, ScriptEngine) - the CHM
# signatures lie in three ways:
#   * find_library / get_library raise SystemError 'stDisplayName' for any
#     argument but the FULL display name ("NETWORK, 1.3.5.3 (OSCAT)"; a
#     bare title "NETWORK" is rejected).
#   * find_library returns IManagedLib objects directly, NOT
#     (IManagedLib, ILibRepository) tuples.
#   * get_library / install_library reject repository=None (TypeError:
#     Value cannot be null. Parameter name: repo) - the "first repository"
#     default must be resolved by the caller.
# So all lookups are resolved by ENUMERATION over get_all_libraries() in
# repository search order (verified working); find/get are never called.
# Direct attribute access only - getattr() fails on the interop objects.

def _lib_repos():
    """Fresh snapshot of the repositories (the CHM: snapshots go stale
    after insert/remove/move)."""
    return list(librarymanager.repositories)


def _repo_by_name(name):
    for repo in _lib_repos():
        try:
            if str(repo.name) == name:
                return repo
        except Exception:
            continue
    return None


def _repo_name(repo):
    try:
        return str(repo.name)
    except Exception:
        return ""


def _lib_summary(lib, repo_name=""):
    """Safe field extraction from an IManagedLib (direct access, one
    try/except per member - the interop objects throw on anything)."""
    out = {"name": "", "displayname": "", "version": "", "company": "",
           "namespace": "", "title": "", "dependencies": [],
           "repo": repo_name, "path": ""}
    try:
        out["name"] = str(lib.name)
    except Exception:
        pass
    try:
        out["displayname"] = str(lib.displayname)
    except Exception:
        pass
    try:
        out["version"] = str(lib.version)
    except Exception:
        pass
    try:
        out["company"] = str(lib.company)
    except Exception:
        pass
    try:
        out["namespace"] = str(lib.default_namespace)
    except Exception:
        pass
    try:
        out["title"] = str(lib.title)
    except Exception:
        pass
    try:
        out["dependencies"] = [str(d) for d in lib.dependencies]
    except Exception:
        pass
    try:
        out["path"] = str(librarymanager.get_file_path(lib))
    except Exception:
        pass
    return out


def _require_repo(name):
    repo = _repo_by_name(name)
    if repo is None:
        raise RuntimeError('unknown library repository "{r}" - see the '
                           "repos command".format(r=name))
    return repo


def _lib_query_hit(lib, query, exact):
    """Live-build match rule (2026-10-21): case-insensitive equality
    against the display name, the title or the library name; a non-exact
    query may also be the bare title ('NETWORK' -> 'NETWORK, 1.3.5.3
    (OSCAT)'). Reads the interop members directly in try/except pairs
    (see the header notes - find_library/get_library are not usable for
    lookups here). Live System-repo libs carry name="", so the name key
    only ever matches when a repository actually populates it."""
    q = query.lower().strip()
    if not q:
        return False
    try:
        display = str(lib.displayname).lower().strip()
    except Exception:
        display = ""
    try:
        title = str(lib.title).lower().strip()
    except Exception:
        title = ""
    try:
        name = str(lib.name).lower().strip()
    except Exception:
        name = ""
    if q == display or (title and q == title) or (name and q == name):
        return True
    if not exact and display and display.split(",")[0].strip() == q:
        return True
    return False


def _resolve_library_pairs(name, repo_name="", exact=False):
    """All (lib, repo) pairs matching `name`, enumerated per repository in
    search order (the first repository wins name collisions). Enumeration
    instead of find_library/get_library - see the header notes at the top
    of this section for the live-build defects that force this."""
    repos = [_require_repo(repo_name)] if repo_name else _lib_repos()
    pairs = []
    for repo in repos:
        for lib in librarymanager.get_all_libraries(repo):
            if _lib_query_hit(lib, name, exact):
                pairs.append((lib, repo))
    return pairs


def cmd_libs(args):
    """List managed libraries (ILibManager.get_all_libraries), paged.

    args: {"repo": scope to one repository name,
           "shadowed": true to include shadowed libs (default excluded),
           "filter": case-insensitive substring on displayname/title/name,
           "offset": page start (default 0),
           "limit": page size (default 200; 0 = unlimited)}
    Summaries are built only for the returned page - the System repo
    holds ~4000 libraries and summarizing all of them produced ~7 MB
    results (live 2026-10-21).
    """
    repo_name = args.get("repo", "")
    include_shadowed = bool(args.get("shadowed"))
    needle = str(args.get("filter", "")).lower().strip()
    try:
        offset = max(int(args.get("offset", 0)), 0)
    except (TypeError, ValueError):
        offset = 0
    try:
        limit = int(args.get("limit", 200))
    except (TypeError, ValueError):
        limit = 200
    if limit < 0:
        limit = 0

    if repo_name:
        repo = _require_repo(repo_name)
        libs = list(librarymanager.get_all_libraries(repo))
    else:
        libs = list(librarymanager.get_all_libraries(not include_shadowed))

    if needle:
        matched = []
        for lib in libs:
            texts = []
            try:
                texts.append(str(lib.displayname))
            except Exception:
                pass
            try:
                texts.append(str(lib.title))
            except Exception:
                pass
            try:
                texts.append(str(lib.name))
            except Exception:
                pass
            if needle in " ".join(t for t in texts if t).lower():
                matched.append(lib)
    else:
        matched = libs
    page = matched[offset:offset + limit] if limit else matched[offset:]

    out = [_lib_summary(lib, repo_name) for lib in page]
    log("OK", "libs: {n}/{t} libraries{s}{f} (offset {o}, limit {l})".format(
        n=len(out), t=len(matched),
        s=" (shadowed included)" if include_shadowed else "",
        f=" filter='{q}'".format(q=needle) if needle else "",
        o=offset, l=limit or "none"))
    return {"total": len(matched), "offset": offset, "count": len(out),
            "truncated": bool(limit) and offset + len(out) < len(matched),
            "limit": limit, "shadowed_included": include_shadowed,
            "libraries": out}


def cmd_lib_find(args):
    """Find libraries across repositories by display name or title
    (enumeration-based, see _lib_query_hit).

    args: {"name": required, "repo": optional scope, "exact": bool}
    Matches in later repositories carry shadowed=true when an earlier
    repository in search order has the same library (first match wins).
    """
    name = args.get("name", "")
    if not name:
        raise RuntimeError("lib_find needs args.name (library name or "
                           "display name)")
    exact = bool(args.get("exact"))
    pairs = _resolve_library_pairs(name, args.get("repo", ""), exact)
    out = []
    seen = set()
    for lib, repo in pairs:
        summary = _lib_summary(lib, _repo_name(repo))
        key = (summary["title"] or summary["displayname"]).lower()
        summary["shadowed"] = key in seen
        seen.add(key)
        out.append(summary)
    log("OK", "lib_find '{n}': {m} match(es)".format(n=name, m=len(out)))
    return {"query": name, "exact": exact,
            "count": len(out), "matches": out}


def cmd_lib_install(args):
    """install_library(filepath, repository, overwrite) - install a
    compiled-library file into a repository (default: the FIRST one).

    args: {"path": required (.library file), "repo": optional name,
           "overwrite": replace existing; needs confirm=true}
    """
    path = args.get("path", "")
    if not path:
        raise RuntimeError("lib_install needs args.path (compiled-library "
                           "file, e.g. some.library)")
    if not os.path.isfile(path):
        raise RuntimeError("library file not found: {p}".format(p=path))
    overwrite = bool(args.get("overwrite"))
    if overwrite and not args.get("confirm"):
        raise RuntimeError("lib_install REFUSED - overwrite=true replaces "
                           "an existing library; requires "
                           "args.confirm=true")
    repo = _require_repo(args["repo"]) if args.get("repo") else None
    if repo is None:
        # Live build: install_library rejects repository=None (TypeError:
        # Value cannot be null) - resolve the CHM's "first repository"
        # default here instead.
        repo = _lib_repos()[0]
    lib = librarymanager.install_library(path, repo, overwrite)
    summary = _lib_summary(lib, _repo_name(repo))
    log("OK", "lib_install {d} {v} -> {r}".format(
        d=summary["displayname"], v=summary["version"],
        r=summary["repo"] or "<first repository>"))
    return summary


def cmd_lib_uninstall(args):
    """uninstall_library(repository, library) - remove a library from a
    repository. Requires confirm=true; ambiguous names (same library in
    several repositories) need args.repo.

    args: {"name": required, "repo": optional name, "confirm": true}
    """
    name = args.get("name", "")
    if not name:
        raise RuntimeError("lib_uninstall needs args.name (library name "
                           "or display name)")
    if not args.get("confirm"):
        raise RuntimeError("lib_uninstall REFUSED - it removes a library "
                           "from a repository; requires args.confirm=true")
    repo_name = args.get("repo", "")
    exact = bool(args.get("exact"))
    if repo_name:
        pairs = _resolve_library_pairs(name, repo_name, exact)
        if not pairs:
            raise RuntimeError('library "{n}" not found in repository '
                               "{r}".format(n=name, r=repo_name))
        if len(pairs) > 1:
            # Several versions/variants in ONE repository: name the exact
            # full display name so the destructive call is unambiguous.
            raise RuntimeError('library "{n}" is ambiguous in repository '
                               "{r} - pass the full display name as "
                               "args.name with exact=true".format(
                                   n=name, r=repo_name))
    else:
        pairs = _resolve_library_pairs(name)
        if not pairs:
            raise RuntimeError('library "{n}" not found in any '
                               "repository".format(n=name))
        if len(pairs) > 1:
            raise RuntimeError('library "{n}" is ambiguous - present in '
                               "{r}; pass args.repo".format(
                                   n=name,
                                   r=", ".join(_repo_name(p[1])
                                               for p in pairs)))
    repo = pairs[0][1]
    librarymanager.uninstall_library(repo, pairs[0][0])
    gone = not _resolve_library_pairs(name, _repo_name(repo), exact)
    log("OK", "lib_uninstall {n} from {r}{v}".format(
        n=name, r=_repo_name(repo),
        v="" if gone else " (STILL PRESENT after uninstall?)"))
    return {"uninstalled": name, "repo": _repo_name(repo),
            "verified_gone": gone}


def cmd_repos(args):
    """List library repositories in search order, with per-repo library
    counts (ILibManager.repositories + get_all_libraries(repo))."""
    del args  # no options yet
    out = []
    for index, repo in enumerate(_lib_repos()):
        entry = {"index": index, "name": _repo_name(repo),
                 "root_folder": "", "editable": None, "libraries": None}
        try:
            entry["root_folder"] = str(repo.root_folder)
        except Exception:
            pass
        try:
            entry["editable"] = bool(repo.editable)
        except Exception:
            pass
        try:
            entry["libraries"] = len(
                list(librarymanager.get_all_libraries(repo)))
        except Exception:
            pass
        out.append(entry)
    log("OK", "repos: {n} repositories in search order".format(n=len(out)))
    return {"count": len(out), "repositories": out}


def cmd_repo_add(args):
    """insert_repository(rootfolder, name, index) - register a new
    repository. Requires confirm=true; rootfolder must already exist.

    args: {"rootfolder": required existing dir, "name": required,
           "index": position in search order, -1 = end, "confirm": true}
    """
    root = args.get("rootfolder", "")
    name = args.get("name", "")
    if not root or not name:
        raise RuntimeError("repo_add needs args.rootfolder (existing "
                           "directory) and args.name")
    if not args.get("confirm"):
        raise RuntimeError("repo_add REFUSED - it changes the machine-wide "
                           "library repository list; requires "
                           "args.confirm=true")
    if not os.path.isdir(root):
        raise RuntimeError("args.rootfolder must be an existing directory: "
                           "{r}".format(r=root))
    try:
        index = int(args.get("index", -1))
    except (TypeError, ValueError):
        index = -1
    repo = librarymanager.insert_repository(root, name, index)
    log("OK", "repo_add {n} at {r} (index {i})".format(
        n=name, r=root, i=index))
    return {"name": _repo_name(repo), "root_folder": root, "index": index}


def cmd_repo_remove(args):
    """remove_repository(repository, delete_on_disk) - unregister a
    repository. Requires confirm=true; delete_on_disk=true also deletes
    the directory (destructive). System repositories are not removable.

    args: {"name": required, "delete_on_disk": false, "confirm": true}
    """
    name = args.get("name", "")
    if not name:
        raise RuntimeError("repo_remove needs args.name (repository name)")
    if not args.get("confirm"):
        raise RuntimeError("repo_remove REFUSED - it changes the "
                           "machine-wide library repository list; requires "
                           "args.confirm=true")
    repo = _require_repo(name)
    try:
        if not repo.editable:
            raise RuntimeError('repository "{r}" is not editable (system '
                               "repository) - it cannot be removed"
                               .format(r=name))
    except RuntimeError:
        raise
    except Exception:
        pass  # editable unreadable - let remove_repository decide
    delete_on_disk = bool(args.get("delete_on_disk"))
    librarymanager.remove_repository(repo, delete_on_disk)
    log("OK", "repo_remove {n} (delete_on_disk={d})".format(
        n=name, d=delete_on_disk))
    return {"removed": name, "delete_on_disk": delete_on_disk}


def cmd_repo_update(args):
    """update_repository(repository, new_name, new_location, copy_libraries)
    - rename a repository and/or point it at a new location. Requires
    confirm=true.

    args: {"name": required, "new_name": "", "new_location": "",
           "copy_libraries": false, "confirm": true}
    """
    name = args.get("name", "")
    if not name:
        raise RuntimeError("repo_update needs args.name (repository name)")
    if not args.get("confirm"):
        raise RuntimeError("repo_update REFUSED - it changes the "
                           "machine-wide library repository list; requires "
                           "args.confirm=true")
    new_name = args.get("new_name", "")
    new_location = args.get("new_location", "")
    if not new_name and not new_location:
        raise RuntimeError("repo_update needs args.new_name and/or "
                           "args.new_location")
    if new_location and not os.path.isdir(new_location):
        raise RuntimeError("args.new_location must be an existing "
                           "directory: {r}".format(r=new_location))
    repo = _require_repo(name)
    copy_libraries = bool(args.get("copy_libraries"))
    librarymanager.update_repository(repo, new_name or None,
                                     new_location or None, copy_libraries)
    log("OK", "repo_update {n} -> name='{nn}' location='{nl}' "
              "copy={c}".format(n=name, nn=new_name, nl=new_location,
                                c=copy_libraries))
    return {"updated": name, "new_name": new_name,
            "new_location": new_location,
            "copy_libraries": copy_libraries}


def cmd_repo_move(args):
    """move_repository(repository, new_index) - reorder the repository
    search order (first match wins when resolving libraries).

    args: {"name": required, "index": required position, -1 = end}
    """
    name = args.get("name", "")
    if not name:
        raise RuntimeError("repo_move needs args.name (repository name)")
    try:
        index = int(args.get("index"))
    except (TypeError, ValueError):
        raise RuntimeError("repo_move needs args.index (integer position "
                           "in the search order, -1 = end)")
    repo = _require_repo(name)
    librarymanager.move_repository(repo, index)
    order = [_repo_name(r) for r in _lib_repos()]
    log("OK", "repo_move {n} -> index {i}; order now: {o}".format(
        n=name, i=index, o=", ".join(order)))
    return {"moved": name, "index": index, "order": order}


# --------------------------------------------------------------------------
# Project tree + ST authoring (ScriptTreeObject/ScriptObject/
# ScriptIecLanguageObjectContainer/ScriptTextualObject stubs)
# --------------------------------------------------------------------------
_TREE_MARKER_ATTRS = ("is_folder", "is_device", "is_application",
                      "is_task_configuration", "is_symbol_config",
                      "is_libman", "is_textlist", "is_imagepool",
                      "is_explicit_connector", "is_project_info",
                      "has_textual_declaration",
                      "has_textual_implementation")


def _obj_attr(obj, attr):
    """Direct-access read of one ScriptObject attribute by literal name
    (getattr() fails on the interop wrappers)."""
    if attr == "guid":
        return str(obj.guid)
    if attr == "is_folder":
        return bool(obj.is_folder)
    if attr == "is_device":
        return bool(obj.is_device)
    if attr == "is_application":
        return bool(obj.is_application)
    if attr == "is_task_configuration":
        return bool(obj.is_task_configuration)
    if attr == "is_symbol_config":
        return bool(obj.is_symbol_config)
    if attr == "is_libman":
        return bool(obj.is_libman)
    if attr == "is_textlist":
        return bool(obj.is_textlist)
    if attr == "is_imagepool":
        return bool(obj.is_imagepool)
    if attr == "is_explicit_connector":
        return bool(obj.is_explicit_connector)
    if attr == "is_project_info":
        return bool(obj.is_project_info)
    if attr == "has_textual_declaration":
        return bool(obj.has_textual_declaration)
    if attr == "has_textual_implementation":
        return bool(obj.has_textual_implementation)
    raise AttributeError(attr)


def _obj_summary(obj):
    """Name + kind markers for one tree object (all guarded)."""
    out = {"name": _short_name(obj)}
    try:
        bp = obj.build_properties
        out["exclude_from_build"] = bool(bp.exclude_from_build)
    except Exception:
        out["exclude_from_build"] = None
    try:
        out["name"] = str(obj.get_name())
    except Exception:
        pass
    try:
        out["guid"] = str(obj.guid)
    except Exception:
        pass
    for attr in _TREE_MARKER_ATTRS:
        try:
            out[attr] = _obj_attr(obj, attr)
        except Exception:
            out[attr] = None
    return out


def _parent_name(obj):
    """Name of the object's tree parent, or "" (scripting objects expose
    the parent through ``obj.parent``; get_parent is not available in the
    e!COCKPIT scripting build)."""
    try:
        parent = obj.parent
    except Exception:
        return ""
    try:
        return parent.get_name() or ""
    except Exception:
        return ""


def _find_candidates(project, name):
    """All tree hits for *name*: ``parent/child`` resolves children by their
    own name plus the direct parent's name (project.find matches bare names
    only); bare names return every hit so the caller can pick."""
    if "/" in name:
        child_name = name.rsplit("/", 1)[1].strip()
        parent_want = name.rsplit("/", 1)[0].rsplit("/", 1)[-1].strip()
        candidates = project.find(child_name, True) or []
        parents = []
        hits = []
        for o in candidates:
            pname = _parent_name(o)
            parents.append(pname)
            if pname.lower() == parent_want.lower():
                hits.append(o)
        if not hits:
            raise RuntimeError(
                'no object "{c}" with parent "{p}" in the project '
                '(parents seen: {seen})'.format(c=child_name, p=parent_want,
                                                seen=", ".join(parents)
                                                or "none"))
        return hits
    found = project.find(name, True)
    if not found:
        raise RuntimeError('no object named "{n}" in the '
                           "project".format(n=name))
    return found


def _find_textual_obj(project, name, want_decl):
    """Resolve an object for a textual read/write and prefer a hit that
    actually carries the requested textual part: task-configuration program
    instances share the POU's name but expose no text (all 19 s4 PROGRAMs
    were unreachable through found[0] before this)."""
    flag = ("has_textual_declaration" if want_decl
            else "has_textual_implementation")
    found = _find_candidates(project, name)
    if len(found) == 1:
        return found[0]
    for o in found:
        try:
            if getattr(o, flag, False):
                return o
        except Exception:
            pass
    for o in found:
        try:
            if getattr(o, "has_textual_declaration", False) \
                    or getattr(o, "has_textual_implementation", False):
                return o
        except Exception:
            pass
    return found[0]


def _find_one(project, name):
    return _find_candidates(project, name)[0]


def _resolve_textual(obj, part):
    """Return the ScriptTextDocument for 'declaration' or 'implementation'.

    Guards the marker first - textual_declaration/textual_implementation
    only exist on objects with has_textual_declaration/implementation.
    """
    try:
        if part == "declaration":
            if not obj.has_textual_declaration:
                raise RuntimeError("object '{n}' has no textual "
                                   "declaration".format(
                                       n=_short_name(obj)))
            return obj.textual_declaration
        if part == "implementation":
            if not obj.has_textual_implementation:
                raise RuntimeError("object '{n}' has no textual "
                                   "implementation".format(
                                       n=_short_name(obj)))
            return obj.textual_implementation
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError("textual {p} access failed on '{n}': "
                           "{e}".format(p=part, n=_short_name(obj),
                                        e=exc))
    raise RuntimeError("textual part must be declaration or implementation")


def cmd_tree(project, args):
    """Inspect / manipulate the project tree (ScriptTreeObject,
    ScriptObject stubs).

    ops:
      list    {name?: find-filter, recursive?: bool (default true),
               limit?: int (default 200)} -> object summaries
      rename  {name, new_name, confirm}          -> renames an object
      remove  {name, confirm}                    -> DESTRUCTIVE: deletes
      move    {name, new_parent, index?: -1, confirm} -> moves in tree
      folder  {parent?, new_name, confirm}       -> creates a folder
    list is read-only; everything else modifies the project and needs
    args.confirm=true.
    """
    op = args.get("op", "list")
    if op == "list":
        name = str(args.get("name", "")).strip()
        limit = int(args.get("limit", 200) or 200)
        if name:
            items = list(project.find(name, bool(args.get("recursive", True))))
        else:
            items = list(project.get_children(
                bool(args.get("recursive", True))))
        out = [_obj_summary(obj) for obj in items[:limit]]
        log("OK", "tree list: {n} objects (matched {t})".format(
            n=len(out), t=len(items)))
        return {"count": len(out), "total": len(items), "objects": out}

    if op == "rename":
        if not args.get("confirm"):
            raise RuntimeError("tree rename REFUSED - requires "
                               "args.confirm=true (it modifies the "
                               "project)")
        obj = _find_one(project, str(args.get("name", "")))
        new_name = str(args.get("new_name", "")).strip()
        if not new_name:
            raise RuntimeError("tree rename needs args.new_name")
        obj.rename(new_name)
        log("OK", "renamed to {n}".format(n=new_name))
        return {"renamed": True, "new_name": new_name}

    if op == "remove":
        if not args.get("confirm"):
            raise RuntimeError("tree remove REFUSED - requires "
                               "args.confirm=true (it DELETES the object "
                               "from the project)")
        obj = _find_one(project, str(args.get("name", "")))
        summary = _obj_summary(obj)
        obj.remove()
        log("OK", "removed object (was: {n})".format(
            n=summary.get("name")))
        return {"removed": True, "was": summary}

    if op == "move":
        if not args.get("confirm"):
            raise RuntimeError("tree move REFUSED - requires "
                               "args.confirm=true (it modifies the "
                               "project)")
        obj = _find_one(project, str(args.get("name", "")))
        parent = _find_one(project, str(args.get("new_parent", "")))
        try:
            index = int(args.get("index", -1))
        except (TypeError, ValueError):
            index = -1
        obj.move(parent, index)
        log("OK", "moved into {p} at {i}".format(
            p=args.get("new_parent"), i=index))
        return {"moved": True, "new_parent": args.get("new_parent"),
                "index": index}

    if op == "folder":
        if not args.get("confirm"):
            raise RuntimeError("tree folder REFUSED - requires "
                               "args.confirm=true (it modifies the "
                               "project)")
        new_name = str(args.get("new_name", "")).strip()
        if not new_name:
            raise RuntimeError("tree folder needs args.new_name")
        parent_name = str(args.get("parent", "")).strip()
        if parent_name:
            parent = _find_one(project, parent_name)
            parent.create_folder(new_name)
        else:
            project.create_folder(new_name)
        log("OK", "folder '{n}' created".format(n=new_name))
        return {"folder": new_name,
                "parent": parent_name or "<project root>"}

    raise RuntimeError('tree needs args.op of "list", "rename", "remove", '
                       '"move" or "folder"')


def _iecc_container(project, parent_name):
    """Container for the ScriptIecLanguageObjectContainer create_* calls.

    LIVE 2026-10-22 (G-22): EScriptProject has NO create_pou
    ("'EScriptProject' object has no attribute 'create_pou'") - the
    mixin lands on the APPLICATION - so the no-parent default is the
    application, not the project (falling back to the project keeps
    offline fakes working)."""
    if parent_name:
        return _find_one(project, parent_name)
    try:
        return get_application(project)
    except Exception:
        return project


_SPECIAL_POU_TYPES = ("check_bounds", "check_pointer",
                      "check_range_unsigned", "check_range_signed",
                      "check_div_real_64", "check_div_real_32",
                      "check_div_int_64", "check_div_int_32",
                      "check_l_range_unsigned", "check_l_range_signed")


def cmd_object_create(project, args):
    """Create project objects (ScriptIecLanguageObjectContainer stub).

    args: {"kind": "pou"|"gvl"|"dut"|"interface"|"special_pou"|"folder"|
           "method"|"property"|"action"|"transition",
           "name": required (EXCEPT special_pou - the engine names
                   implicit-check POUs itself),
           "special_type": SpecialPouType member (special_pou only;
                   adding a check function FORCES A FULL RECOMPILE and
                   prohibits online change from then on - stub warning),
           "pou_type": "program"|"functionblock"|"function" (pou only),
           "base_type"?: (dut/interface/method return),
           "parent"?: object name (container; default the APPLICATION -
                   EScriptProject itself has no create_* live, G-22),
           "declaration"?: ST text (textual objects),
           "implementation"?: ST text,
           "confirm": true}

    CONFIRM-GATED - it modifies the project. ImplementationLanguages.st
    is the default language; method/property take return_type.
    """
    if not args.get("confirm"):
        raise RuntimeError("object_create REFUSED - requires "
                           "args.confirm=true (it modifies the project)")
    kind = str(args.get("kind", "")).lower()
    name = str(args.get("name", "")).strip()

    if kind == "special_pou":
        # create_pou(SpecialPouType) - implicit-check POUs (3.5.12.10).
        special = str(args.get("special_type", "")).strip()
        if not special:
            raise RuntimeError("object_create special_pou needs "
                               "args.special_type (one of: {k})"
                               .format(k=", ".join(_SPECIAL_POU_TYPES)))
        parent_name = str(args.get("parent", "")).strip()
        container = _iecc_container(
            project, str(args.get("parent", "")).strip())
        try:
            member = getattr(SpecialPouType, special)
        except Exception:
            raise RuntimeError("object_create: unknown SpecialPouType "
                               "'{s}' (known: {k})".format(
                                   s=special,
                                   k=", ".join(_SPECIAL_POU_TYPES)))
        created = container.create_pou(member)
        log("OK", "created special_pou {s} (FORCES A FULL RECOMPILE; "
            "online change is prohibited from then on)".format(s=special))
        return {"created": special, "kind": "special_pou",
                "warning": "check functions force a full recompile and "
                           "prohibit online change",
                "summary": _obj_summary(created)}

    if not name:
        raise RuntimeError("object_create needs args.name")
    parent_name = str(args.get("parent", "")).strip()
    container = _iecc_container(
        project, str(args.get("parent", "")).strip())

    created = None
    if kind == "pou":
        pou_types = {"program": "Program", "functionblock": "FunctionBlock",
                     "function": "Function"}
        pou_type = str(args.get("pou_type", "functionblock")).lower()
        if pou_type not in pou_types:
            raise RuntimeError("object_create: pou_type must be program, "
                               "functionblock or function")
        try:
            langs = ImplementationLanguages
        except NameError:
            langs = None
        if langs is not None:
            created = container.create_pou(
                name, type=getattr(PouType, pou_types[pou_type]),
                language=langs.st, return_type=args.get("return_type") or None)
        else:
            created = container.create_pou(
                name, type=getattr(PouType, pou_types[pou_type]),
                return_type=args.get("return_type") or None)
    elif kind == "gvl":
        created = container.create_gvl(name)
    elif kind == "dut":
        created = container.create_dut(
            name, type=DutType.Structure,
            baseType=args.get("base_type") or None)
    elif kind == "interface":
        created = container.create_interface(
            name, args.get("base_type") or "__System.IQueryInterface")
    elif kind in ("method", "property", "action", "transition"):
        if not parent_name:
            raise RuntimeError("object_create {k} needs args.parent (the "
                               "POU to attach to)".format(k=kind))
        if kind == "method":
            created = container.create_method(
                name, return_type=args.get("return_type") or None)
        elif kind == "property":
            created = container.create_property(
                name, return_type=args.get("return_type") or "int")
        elif kind == "action":
            created = container.create_action(name)
        else:
            created = container.create_transition(name)
    else:
        raise RuntimeError('object_create needs args.kind of "pou", "gvl", '
                           '"dut", "interface", "special_pou", "folder", '
                           '"method", "property", "action" or '
                           '"transition"')
    log("OK", "created {k} '{n}'".format(k=kind, n=name))

    declaration = args.get("declaration")
    implementation = args.get("implementation")
    if declaration:
        _resolve_textual(created, "declaration").replace(
            new_text=str(declaration))
        log("OK", "declaration text set ({c} chars)".format(
            c=len(str(declaration))))
    if implementation:
        _resolve_textual(created, "implementation").replace(
            new_text=str(implementation))
        log("OK", "implementation text set ({c} chars)".format(
            c=len(str(implementation))))
    return {"created": name, "kind": kind,
            "summary": _obj_summary(created)}


def cmd_textual(project, args):
    """Read / write the ST text of a textual object.

    args: {"op": "read"|"write", "name": object name,
           "part": "declaration"|"implementation" (default: both for
           read, implementation for write),
           "text": ST text (write only), "confirm": true (write)}.

    read is read-only; write replaces the whole part
    (ScriptTextDocument.replace(new_text=...)) and is CONFIRM-GATED -
    it modifies project code.
    """
    op = args.get("op", "read")
    if op == "read" and "," in str(args.get("name", "")):
        # bulk read: a comma-separated name list (CODESYS object names never
        # contain commas) - one exec() serves every object, so a whole
        # domain pull is one relay command instead of N. Per-object errors
        # (missing object, no textual part) land in the item's "error" field
        # instead of aborting the batch.
        out = {"objects": [], "cmd": "textual", "errors": 0}
        raw = str(args.get("name", ""))
        for one in [n.strip() for n in raw.split(",") if n.strip()]:
            item = {"name": one, "declaration": None,
                    "implementation": None, "error": None}
            try:
                obj = _find_textual_obj(project, one, True)
                for p, key in (("declaration", "declaration"),
                               ("implementation", "implementation")):
                    try:
                        item[key] = _resolve_textual(obj, p).text
                    except Exception as exc:
                        item["error"] = ("{part}: {msg}".format(
                            part=p, msg=exc))
                        out["errors"] += 1
            except Exception as exc:
                item["error"] = str(exc)
                out["errors"] += 1
            out["objects"].append(item)
        return out
    # Both ops resolve through _find_textual_obj: task-configuration
    # program instances share the POU's name and expose no text, and the
    # write path resolved through _find_one/found[0] before - a bare
    # PROGRAM push landed on the binding node and failed with
    # "has no textual ..." (the read/bulk path got this in 1ef00f3).
    if op == "read":
        want_decl = (args.get("part", "") != "implementation")
        obj = _find_textual_obj(project, str(args.get("name", "")), want_decl)
        out = {"name": args.get("name")}
        part = args.get("part", "")
        parts = (part,) if part in ("declaration", "implementation") else \
            ("declaration", "implementation")
        for p in parts:
            doc = _resolve_textual(obj, p)
            out[p] = doc.text
        return out
    if op == "write":
        if not args.get("confirm"):
            raise RuntimeError("textual write REFUSED - requires "
                               "args.confirm=true (it replaces project "
                               "code)")
        text = args.get("text")
        if text is None:
            raise RuntimeError("textual write needs args.text")
        part = args.get("part", "implementation")
        obj = _find_textual_obj(project, str(args.get("name", "")),
                                part == "declaration")
        doc = _resolve_textual(obj, part)
        doc.replace(new_text=str(text))
        log("OK", "textual {p} of '{n}' replaced ({c} chars)".format(
            p=part, n=args.get("name"), c=len(str(text))))
        return {"written": True, "name": args.get("name"), "part": part,
                "chars": len(str(text))}
    raise RuntimeError('textual needs args.op of "read" or "write"')


def _get_application_obj(project, args):
    """The application ScriptObject (args.application name overrides the
    first found application)."""
    app_name = str(args.get("application", "")).strip()
    if app_name:
        return _find_one(project, app_name)
    return get_application(project)


def cmd_task_config(project, args):
    """Inspect / create the task configuration (ScriptTaskConfigObject
    stub, 3.5.10.0).

    ops:
      inspect {application?} -> tasks with kind, priority, interval,
              watchdog and assigned POUs (read-only)
      create_task {application?, name, kind?: cyclic|freewheeling|event|
              external_event|status|parent_synchron, interval?: "200",
              interval_unit?: "ms", priority?: "1", watchdog_enabled?,
              watchdog_time?, pous?: [names], confirm} -> creates the
              task configuration object when missing, then the task

    create_task modifies the project - CONFIRM-GATED. KindOfTask is an
    injected enum (Cyclic=1, Freewheeling=2, Event=3, ExternalEvent=4,
    Status=5, ParentSynchron=6).
    """
    op = args.get("op", "inspect")
    app = _get_application_obj(project, args)
    tc = None
    for child in app.get_children(True):
        try:
            if child.is_task_configuration:
                tc = child
                break
        except Exception:
            continue

    if op == "inspect":
        tasks = []
        if tc is not None:
            for child in tc.get_children(True):
                try:
                    if not child.is_task:
                        continue
                except Exception:
                    continue
                task = {"name": _short_name(child)}
                for attr in ("priority", "interval", "interval_unit",
                             "event", "external_event", "core_binding",
                             "parent_synchron_task"):
                    try:
                        if attr == "core_binding":
                            task[attr] = str(child.core_binding)
                        elif attr == "parent_synchron_task":
                            task[attr] = str(child.parent_synchron_task)
                        else:
                            task[attr] = str(getattr(child, attr))
                    except Exception:
                        task[attr] = None
                try:
                    wd = child.watchdog
                    task["watchdog"] = {
                        "enabled": bool(wd.enabled),
                        "time": str(wd.time),
                        "time_unit": str(wd.time_unit),
                        "sensitivity": str(wd.sensitivity),
                    }
                except Exception:
                    task["watchdog"] = None
                try:
                    pous = []
                    for entry in child.pous:
                        pous.append(str(entry[0]))
                    task["pous"] = pous
                except Exception:
                    task["pous"] = None
                tasks.append(task)
        log("OK", "task config: {n} task(s), config object "
            "{p}".format(n=len(tasks),
                         p="present" if tc is not None else "MISSING"))
        return {"application": _short_name(app),
                "has_task_configuration": tc is not None,
                "tasks": tasks}

    if op == "create_task":
        if not args.get("confirm"):
            raise RuntimeError("task create_task REFUSED - requires "
                               "args.confirm=true (it modifies the "
                               "project)")
        name = str(args.get("name", "")).strip()
        if not name:
            raise RuntimeError("task create_task needs args.name")
        if tc is None:
            tc = app.create_task_configuration()
            log("OK", "task configuration object created")
        task = tc.create_task(name)
        kind_map = {"cyclic": "Cyclic", "freewheeling": "Freewheeling",
                    "event": "Event", "external_event": "ExternalEvent",
                    "status": "Status", "parent_synchron": "ParentSynchron"}
        kind = str(args.get("kind", "cyclic")).lower()
        if KindOfTask is not None and kind in kind_map:
            task.kind_of_task = getattr(KindOfTask, kind_map[kind])
        if args.get("interval"):
            task.interval = str(args["interval"])
        if args.get("interval_unit"):
            task.interval_unit = str(args["interval_unit"])
        if args.get("priority"):
            task.priority = str(args["priority"])
        if args.get("watchdog_enabled") is not None:
            task.watchdog.enabled = bool(args["watchdog_enabled"])
        if args.get("watchdog_time"):
            task.watchdog.time = str(args["watchdog_time"])
        for pou in (args.get("pous") or []):
            task.pous.add(str(pou))
        log("OK", "task '{n}' created ({k})".format(n=name, k=kind))
        return {"created": name, "kind": kind,
                "pous": [str(p) for p in (args.get("pous") or [])]}
    raise RuntimeError('task_config needs args.op of "inspect" or '
                       '"create_task"')


# ScriptSymbolConfigObject stub value tables (duplicated inside the stub).
_SYMBOL_CONTENT_FLAGS = (
    ("SupportOPCUA", 1),
    ("IncludeComments", 2),
    ("IncludeAttributes", 4),
    ("IncludeTypeNodeAttributes", 8),
    ("IncludeExecutables", 16),
    ("UseEmptyNamespaceByDefault", 32),
    ("XmlIncludeNodeFlags", 1 << 16),
    ("XmlIncludeComments", 2 << 16),
    ("XmlIncludeAttributes", 4 << 16),
    ("XmlIncludeTypeNodeAttributes", 8 << 16),
    ("XmlIncludeExecutables", 16 << 16),
)
_SYMBOL_ATTRIBUTE_FILTER_TYPES = (
    ("none", 0), ("All", 1), ("SimpleIdentifiers", 2),
    ("Prefix", 3), ("Regex", 4),
)
_SYMBOL_COMMENT_FILTER_TYPES = (
    ("none", 0), ("NormalComments", 1), ("DocuComments", 2), ("Both", 3),
    ("PreferNormalComments", 4), ("PreferDocuComments", 5),
)
_DIRECT_IO_OBSTACLES = (
    ("none", 0), ("OldCompilerVersion", 1),
    ("TargetSettingSeparateApplication", 2),
)


def _symbol_config_dict(sym):
    """Guarded read of the symbol configuration options
    (ScriptSymbolConfigObject, 3.5.10.0)."""
    out = {}
    readers = (
        ("content_feature_flags",
         lambda: _flags_to_names(sym.content_feature_flags,
                                 _SYMBOL_CONTENT_FLAGS)),
        ("content_feature_flags_raw",
         lambda: str(sym.content_feature_flags)),
        ("effective_content_feature_flags",
         lambda: _flags_to_names(sym.effective_content_feature_flags,
                                 _SYMBOL_CONTENT_FLAGS)),
        ("symbol_attribute_filter_type",
         lambda: _enum_to_name(sym.symbol_attribute_filter_type,
                               _SYMBOL_ATTRIBUTE_FILTER_TYPES)),
        ("effective_symbol_attribute_filter_type",
         lambda: _enum_to_name(sym.effective_symbol_attribute_filter_type,
                               _SYMBOL_ATTRIBUTE_FILTER_TYPES)),
        ("symbol_attribute_filter_data",
         lambda: str(sym.symbol_attribute_filter_data)),
        ("symbol_comment_filter_type",
         lambda: _enum_to_name(sym.symbol_comment_filter_type,
                               _SYMBOL_COMMENT_FILTER_TYPES)),
        ("effective_symbol_comment_filter_type",
         lambda: _enum_to_name(sym.effective_symbol_comment_filter_type,
                               _SYMBOL_COMMENT_FILTER_TYPES)),
        ("enable_direct_io_access",
         lambda: bool(sym.enable_direct_io_access)),
    )
    for name, reader in readers:
        try:
            out[name] = reader()
        except Exception:
            out[name] = None
    try:
        out["direct_io_obstacles"] = _enum_to_name(
            sym.check_effective_direct_io_access(), _DIRECT_IO_OBSTACLES)
    except Exception:
        out["direct_io_obstacles"] = None
    return out


def cmd_symbol_config(project, args):
    """Create the symbol configuration / dump the variable inventory /
    read-set the config options / dump the XML schema
    (ScriptSymbolConfigObject stub, 3.5.10.0).

    ops:
      variables  {application?, compile?: true} -> full signature +
                 variable inventory (get_all_signatures; compile=true
                 builds first - takes time, read-only)
      create     {application?, export_comments?: false,
                 support_opc_ua?: true, confirm} -> adds the symbol
                 configuration object to the application
      config     {application?} -> content feature flags (decoded),
                 attribute/comment filter types + data, direct-IO
                 access (read-only)
      config_set {application?, confirm, content_feature_flags?
                 ('+'-combined names), attribute_filter_type?,
                 attribute_filter_data?, comment_filter_type?}
                 -> the option setters (CONFIRM-GATED - modifies the
                 project); read-back after
      xsd        {application?, file?} -> write
                 get_symbol_configuration_xsd() (the schema as
                 appropriate for THIS release) to a file (read-only;
                 default <ECP_PROJECT_ROOT>/plcopensrc-export/
                 Symbolconfiguration.xsd, else under WORK_DIR)

    create/config_set modify the project - CONFIRM-GATED.
    """
    op = args.get("op", "variables")
    app = _get_application_obj(project, args)
    sym = None
    for child in app.get_children(True):
        try:
            if child.is_symbol_config:
                sym = child
                break
        except Exception:
            continue

    if op == "create":
        if not args.get("confirm"):
            raise RuntimeError("symbol_config create REFUSED - requires "
                               "args.confirm=true (it modifies the "
                               "project)")
        if sym is not None:
            return {"created": False,
                    "note": "symbol configuration already present",
                    "name": _short_name(sym)}
        # LIVE 2026-10-22: the calculator parameter is a Guid here -
        # the stub-stand-in `False` raises "expected Guid, got bool",
        # and neither None nor the Guid.Empty STRING coerce. Ladder:
        # False -> None -> CLR System.Guid.Empty -> Guid.Empty string
        # -> 2-arg form. (Under CPython the System import fails and the
        # CLR rung is skipped - the offline test asserts that form list.)
        attempts = []
        sym = None
        forms = [False, None]
        try:
            from System import Guid as _DotNetGuid
            forms.append(_DotNetGuid.Empty)
        except Exception:
            pass
        forms.append("00000000-0000-0000-0000-000000000000")
        for calc in forms:
            try:
                sym = app.create_symbol_config(
                    bool(args.get("export_comments", False)),
                    bool(args.get("support_opc_ua", True)),
                    calc)
                break
            except Exception as exc:
                attempts.append("{v}: {e}".format(v=calc,
                                                  e=str(exc)[:90]))
        if sym is None:
            try:
                sym = app.create_symbol_config(
                    bool(args.get("export_comments", False)),
                    bool(args.get("support_opc_ua", True)))
                log("INFO", "create_symbol_config: 2-arg form used")
            except Exception as exc:
                raise RuntimeError("create_symbol_config failed in all "
                                   "forms: " + "; ".join(attempts) +
                                   "; 2-arg: {e}".format(
                                       e=str(exc)[:120]))
        log("OK", "symbol configuration created")
        return {"created": True, "name": _short_name(sym)}

    if op == "variables":
        if sym is None:
            raise RuntimeError("no symbol configuration object under the "
                               "application - create it first "
                               "(op=create)")
        compile_first = bool(args.get("compile", True))
        signatures = sym.get_all_signatures(compile=compile_first)
        out = []
        for sig in signatures:
            entry = {"name": str(sig.name),
                     "full_qualified_name": str(sig.full_qualified_name),
                     "variables": []}
            try:
                for var in sig.variables:
                    v = {"name": str(var.name), "type": str(var.type)}
                    try:
                        v["effective_access"] = str(var.effective_access)
                    except Exception:
                        pass
                    entry["variables"].append(v)
            except Exception:
                pass
            out.append(entry)
        total = sum(len(e["variables"]) for e in out)
        log("OK", "symbol inventory: {s} signatures, {v} "
            "variables".format(s=len(out), v=total))
        return {"signatures": len(out), "variables": total,
                "compiled": compile_first, "content": out}

    if op == "config":
        if sym is None:
            raise RuntimeError("no symbol configuration object under the "
                               "application - create it first "
                               "(op=create)")
        out = _symbol_config_dict(sym)
        log("OK", "symbol config options read")
        return {"application": _short_name(app), "name": _short_name(sym),
                "config": out}

    if op == "config_set":
        if sym is None:
            raise RuntimeError("no symbol configuration object under the "
                               "application - create it first "
                               "(op=create)")
        if not args.get("confirm"):
            raise RuntimeError("symbol_config config_set REFUSED - "
                               "requires args.confirm=true (it modifies "
                               "the project)")
        applied = {}
        flags_raw = str(args.get("content_feature_flags", "") or "").strip()
        if flags_raw:
            names = [t for t in re.split(r"[+,\s]+", flags_raw)
                     if t.strip()]
            if names == ["none"]:
                value = 0
                if SymbolConfigContentFeatureFlags is not None:
                    try:
                        value = SymbolConfigContentFeatureFlags.none
                    except Exception:
                        value = 0
            else:
                value = _enum_by_names("SymbolConfigContentFeatureFlags",
                                       names, _SYMBOL_CONTENT_FLAGS,
                                       SymbolConfigContentFeatureFlags)
            sym.content_feature_flags = value
            applied["content_feature_flags"] = names
        attr_type = str(args.get("attribute_filter_type", "") or "").strip()
        if attr_type:
            value = _enum_by_names("SymbolAttributeFilterTypes",
                                   [attr_type],
                                   _SYMBOL_ATTRIBUTE_FILTER_TYPES,
                                   SymbolAttributeFilterTypes)
            sym.symbol_attribute_filter_type = value
            applied["attribute_filter_type"] = attr_type
        if args.get("attribute_filter_data") is not None:
            value = str(args["attribute_filter_data"])
            sym.symbol_attribute_filter_data = value
            applied["attribute_filter_data"] = value
        comment_type = str(args.get("comment_filter_type", "") or "").strip()
        if comment_type:
            value = _enum_by_names("SymbolCommentFilterType",
                                   [comment_type],
                                   _SYMBOL_COMMENT_FILTER_TYPES,
                                   SymbolCommentFilterType)
            sym.symbol_comment_filter_type = value
            applied["comment_filter_type"] = comment_type
        if not applied:
            raise RuntimeError("symbol_config config_set needs at least "
                               "one of content_feature_flags, "
                               "attribute_filter_type(+data), "
                               "comment_filter_type")
        log("OK", "symbol config options set: {a}".format(
            a=sorted(applied)))
        return {"application": _short_name(app), "applied": applied,
                "config": _symbol_config_dict(sym)}

    if op == "xsd":
        if sym is None:
            raise RuntimeError("no symbol configuration object under the "
                               "application - create it first "
                               "(op=create)")
        data = sym.get_symbol_configuration_xsd()
        if isinstance(data, str):
            payload = _to_bytes(data)
        else:
            try:
                payload = data.tobytes()
            except Exception:
                try:
                    payload = bytearray(data)
                except Exception as exc:
                    raise RuntimeError("unexpected XSD payload type "
                                       "{t}: {e}".format(
                                           t=type(data).__name__, e=exc))
        path = str(args.get("file", "") or "").strip()
        if not path:
            base = (os.path.join(PROJECT_ROOT, "plcopensrc-export")
                    if PROJECT_ROOT else WORK_DIR)
            if not base:
                raise RuntimeError("symbol_config xsd needs args.file "
                                   "(or ECP_PROJECT_ROOT / ECP_WORK_DIR "
                                   "for the default location)")
            if not os.path.isdir(base):
                os.makedirs(base)
            path = os.path.join(base, "Symbolconfiguration.xsd")
        path = os.path.abspath(path)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        with open(path, "wb") as fh:
            fh.write(payload)
        size = os.path.getsize(path)
        preview = ""
        try:
            preview = payload[:64].decode("utf-8", "replace")
        except Exception:
            pass
        log("OK", "symbol configuration XSD written: {p} ({n} "
            "bytes)".format(p=path, n=size))
        return {"file": path, "bytes": size, "preview": preview}
    raise RuntimeError('symbol_config needs args.op of "variables", '
                       '"create", "config", "config_set" or "xsd"')


# --------------------------------------------------------------------------
# Device domain (ScriptDeviceObject/ScriptDeviceParameters/ScriptOnline
# gateway stack stubs)
# --------------------------------------------------------------------------
def _device_obj(project, args):
    """The device ScriptObject (args.device name overrides the first
    device)."""
    device_name = str(args.get("device", "")).strip()
    if device_name:
        return _find_one(project, device_name)
    return get_device(project, "")


def _gateway_label(gw):
    """Readable gateway name (str(gw.name); the interop repr has no
    Name= slice for ScriptGateway)."""
    try:
        name = str(gw.name)
        if name:
            return name
    except Exception:
        pass
    return _short_name(gw)


def cmd_device_scan(project, args):
    """Gateway + network device discovery (ScriptOnline gateway stack,
    3.5.8.0) - read-only.

    ops:
      gateways  -> configured gateways + drivers
      scan      {gateway?: name} -> network scan (blocks for the scan
              timeout); returns discovered targets (name, address,
              vendor, id, version)
      address   {gateway?, ip, port?} -> resolve a CODESYS address from
              an IP (find_address_by_ip)
    """
    op = args.get("op", "gateways")
    gw_name = str(args.get("gateway", "")).strip()

    def _pick_gateway():
        gateways = online.gateways
        if gw_name:
            try:
                return gateways.find_with_name(gw_name)
            except Exception:
                raise RuntimeError("gateway '{g}' not found".format(
                    g=gw_name))
        # LIVE 2026-10-22: len() on the .NET gateway collection fails
        # ("len() of unsized object") - iterate for the first entry.
        first = None
        try:
            for gw in gateways:
                first = gw
                break
        except Exception:
            first = None
        if first is None:
            raise RuntimeError("no gateways configured in e!COCKPIT")
        return first

    if op == "gateways":
        out = []
        try:
            for gw in online.gateways:
                entry = {"name": str(gw.name), "guid": str(gw.guid)}
                try:
                    entry["driver"] = str(gw.gateway_driver)
                except Exception:
                    pass
                out.append(entry)
        except Exception as exc:
            out.append({"error": str(exc)[:150]})
        drivers = []
        try:
            drivers = [str(d.name) for d in online.gateway_drivers]
        except Exception:
            pass
        log("OK", "gateways: {n}, drivers: {d}".format(
            n=len(out), d=len(drivers)))
        return {"gateways": out, "drivers": drivers}

    if op == "scan":
        gw = _pick_gateway()
        log("INFO", "network scan on gateway '{g}' (blocks for the scan "
            "timeout)".format(g=_gateway_label(gw)))
        targets = gw.perform_network_scan()
        out = []
        for t in targets:
            entry = {}
            for attr in ("device_name", "type_name", "vendor_name",
                         "address", "parent_address", "block_driver",
                         "block_driver_address", "locked_in_cache"):
                try:
                    entry[attr] = str(getattr(t, attr))
                except Exception:
                    entry[attr] = None
            try:
                entry["device_id"] = "{t} {i} v{v}".format(
                    t=t.device_id.type, i=t.device_id.id,
                    v=t.device_id.version)
            except Exception:
                entry["device_id"] = None
            out.append(entry)
        log("OK", "scan found {n} target(s)".format(n=len(out)))
        return {"gateway": _gateway_label(gw), "count": len(out),
                "targets": out}

    if op == "address":
        ip = str(args.get("ip", "")).strip()
        if not ip:
            raise RuntimeError("device_scan address needs args.ip")
        gw = _pick_gateway()
        port = int(args.get("port", 11740) or 11740)
        address = gw.find_address_by_ip(ip, port)
        log("OK", "address for {ip}: {a}".format(ip=ip, a=address))
        return {"ip": ip, "gateway": _gateway_label(gw), "address": address}
    raise RuntimeError('device_scan needs args.op of "gateways", "scan" '
                       'or "address"')


def cmd_device_comm(project, args):
    """Read / set the device communication settings
    (ScriptDeviceObject.set_gateway_and_* + ScriptCommunicationSettings).

    ops:
      get {device?} -> gateway guid, address, scanned_* values
              (read-only)
      set_ip {device?, gateway?, ip, confirm} -> point the device at an
              IP via the gateway (modifies the project device
              configuration - CONFIRM-GATED)
    """
    op = args.get("op", "get")
    dev = _device_obj(project, args)

    def _settings():
        out = {}
        try:
            s = dev.get_device_communication_settings()
        except Exception as exc:
            return {"error": str(exc)[:150]}
        for attr in ("gateway_guid", "device_address", "device_name",
                     "scanned_device_name", "scanned_ip_address_and_port",
                     "scanned_target_id", "scanned_target_name",
                     "scanned_target_type", "scanned_target_vendor"):
            try:
                out[attr] = str(getattr(s, attr))
            except Exception:
                out[attr] = None
        return out

    if op == "get":
        settings = _settings()
        log("OK", "comm settings read")
        return {"device": _short_name(dev), "settings": settings}

    if op == "set_ip":
        if not args.get("confirm"):
            raise RuntimeError("device_comm set_ip REFUSED - requires "
                               "args.confirm=true (it modifies the "
                               "project device configuration)")
        ip = str(args.get("ip", "")).strip()
        if not ip:
            raise RuntimeError("device_comm set_ip needs args.ip")
        gw = str(args.get("gateway", "")).strip()
        gateways = online.gateways
        gw_obj = None
        if gw:
            try:
                gw_obj = gateways.find_with_name(gw)
            except Exception:
                gw_obj = gateways[gw]
        else:
            gw_obj = gateways[0]
        if args.get("device_name"):
            dev.set_gateway_and_device_name(gw_obj,
                                            str(args["device_name"]))
        else:
            dev.set_gateway_and_ip_address(gw_obj, ip)
        log("OK", "device {d}: gateway/address set to {ip}".format(
            d=_short_name(dev), ip=ip))
        return {"device": _short_name(dev), "set": True,
                "settings": _settings()}
    raise RuntimeError('device_comm needs args.op of "get" or "set_ip"')


# ScriptDriverInfo / ScriptDeviceObject stub value tables.
_STOP_RESET_VALUES = (
    ("KeepCurrentValues", 0), ("SetToDefault", 1), ("ExecuteProgram", 2),
)
_ALWAYS_UPDATE_VALUES = (
    ("Disabled", 1), ("OnlyIfUnused", 2), ("AlwaysInBusCycle", 3),
)


def _driver_info_dict(di):
    """Guarded read of every ScriptDriverInfo member (3.5.9.0)."""
    out = {}
    readers = (
        ("can_set_io_application",
         lambda: bool(di.can_set_io_application)),
        ("io_application", lambda: str(di.io_application)),
        ("update_ios_while_in_stop",
         lambda: bool(di.update_ios_while_in_stop)),
        ("behaviour_for_outputs_on_stop",
         lambda: _enum_to_name(di.behaviour_for_outputs_on_stop,
                               _STOP_RESET_VALUES)),
        ("user_program_for_stop_reset_behaviour",
         lambda: str(di.user_program_for_stop_reset_behaviour)),
        ("always_update_variables",
         lambda: _enum_to_name(di.always_update_variables,
                               _ALWAYS_UPDATE_VALUES)),
        ("bus_cycle_task_by_name",
         lambda: str(di.bus_cycle_task_by_name)),
        ("bus_cycle_task_by_guid",
         lambda: str(di.bus_cycle_task_by_guid)),
        ("generate_force_variables",
         lambda: bool(di.generate_force_variables)),
        ("enable_diagnosis", lambda: bool(di.enable_diagnosis)),
        ("show_io_warnings_as_errors",
         lambda: bool(di.show_io_warnings_as_errors)),
    )
    for name, reader in readers:
        try:
            out[name] = reader()
        except Exception:
            out[name] = None
    return out


def cmd_device_info(project, args):
    """Device identification, simulation mode and driver info
    (ScriptDeviceObject stub, 3.5.4.0 / 3.5.9.0).

    ops:
      identification {device?} -> get_device_identification()
              ({type, id, version}) + get_module_identification()
              (read-only)
      simulation     {device?} -> get_simulation_mode() (read-only)
      simulation_set {device?, enabled, confirm} -> set_simulation_mode
              (modifies the project device configuration - CONFIRM-GATED)
      driver_info    {device?} -> ScriptDriverInfo dump (read-only):
              bus cycle task, IO application, stop behaviour, always-
              update mode, force-variable generation, diagnosis
      driver_info_set {device?, confirm, bus_cycle_task?, io_application?,
              update_ios_while_in_stop?, behaviour_for_outputs_on_stop?,
              user_program_for_stop_reset_behaviour?,
              always_update_variables?, generate_force_variables?,
              enable_diagnosis?, show_io_warnings_as_errors?}
              -> the ScriptDriverInfo setters (CONFIRM-GATED, modifies
              the project device configuration); read-back after
    """
    op = args.get("op", "identification")
    dev = _device_obj(project, args)

    if op == "identification":
        out = {"device": _device_label(dev)}
        try:
            did = dev.get_device_identification()
            out["device_id"] = {"type": str(did.type), "id": str(did.id),
                                "version": str(did.version)}
        except Exception as exc:
            out["device_id"] = {"error": str(exc)[:150]}
        try:
            out["module_id"] = str(dev.get_module_identification())
        except Exception as exc:
            out["module_id"] = "error: {e}".format(e=str(exc)[:140])
        log("OK", "identification read")
        return out

    if op == "simulation":
        try:
            mode = bool(dev.get_simulation_mode())
        except Exception as exc:
            raise RuntimeError("get_simulation_mode failed: {e}".format(
                e=exc))
        log("OK", "simulation mode: {m}".format(m=mode))
        return {"device": _device_label(dev), "simulation": mode}

    if op == "simulation_set":
        if not args.get("confirm"):
            raise RuntimeError("device_info simulation_set REFUSED - "
                               "requires args.confirm=true (it modifies "
                               "the project device configuration)")
        enabled = bool(args.get("enabled", False))
        dev.set_simulation_mode(enabled)
        try:
            readback = bool(dev.get_simulation_mode())
        except Exception:
            readback = None
        log("OK", "device {d}: simulation mode set to {m}".format(
            d=_device_label(dev), m=enabled))
        return {"device": _device_label(dev), "set": enabled,
                "simulation": readback}

    if op == "driver_info":
        try:
            di = dev.driver_info
        except Exception as exc:
            raise RuntimeError("driver_info not available: {e}".format(
                e=exc))
        out = _driver_info_dict(di)
        log("OK", "driver info read (bus cycle task: {t})".format(
            t=out.get("bus_cycle_task_by_name")))
        return {"device": _device_label(dev), "driver_info": out}

    if op == "driver_info_set":
        if not args.get("confirm"):
            raise RuntimeError("device_info driver_info_set REFUSED - "
                               "requires args.confirm=true (it modifies "
                               "the project device configuration)")
        try:
            di = dev.driver_info
        except Exception as exc:
            raise RuntimeError("driver_info not available: {e}".format(
                e=exc))
        applied = {}
        if args.get("bus_cycle_task"):
            task = str(args["bus_cycle_task"])
            di.set_bus_cycle_task(task)
            applied["bus_cycle_task"] = task
        if args.get("io_application"):
            name = str(args["io_application"])
            found = project.find(name, True)
            app_obj = None
            for o in (found or []):
                try:
                    if o.is_application:
                        app_obj = o
                        break
                except Exception:
                    continue
            if app_obj is None:
                raise RuntimeError("no application named '{n}' in the "
                                   "project".format(n=name))
            di.set_io_application(app_obj)
            applied["io_application"] = name
        if args.get("update_ios_while_in_stop") is not None:
            value = bool(args["update_ios_while_in_stop"])
            di.update_ios_while_in_stop = value
            applied["update_ios_while_in_stop"] = value
        if args.get("behaviour_for_outputs_on_stop"):
            value = _enum_by_names(
                "StopResetBehaviour",
                [str(args["behaviour_for_outputs_on_stop"])],
                _STOP_RESET_VALUES, StopResetBehaviour)
            di.behaviour_for_outputs_on_stop = value
            applied["behaviour_for_outputs_on_stop"] = \
                str(args["behaviour_for_outputs_on_stop"])
        if args.get("user_program_for_stop_reset_behaviour") is not None:
            value = str(args["user_program_for_stop_reset_behaviour"])
            di.user_program_for_stop_reset_behaviour = value
            applied["user_program_for_stop_reset_behaviour"] = value
        if args.get("always_update_variables"):
            value = _enum_by_names(
                "AlwaysUpdateVariablesMode",
                [str(args["always_update_variables"])],
                _ALWAYS_UPDATE_VALUES, AlwaysUpdateVariablesMode)
            di.always_update_variables = value
            applied["always_update_variables"] = \
                str(args["always_update_variables"])
        if args.get("generate_force_variables") is not None:
            value = bool(args["generate_force_variables"])
            di.generate_force_variables = value
            applied["generate_force_variables"] = value
        if args.get("enable_diagnosis") is not None:
            value = bool(args["enable_diagnosis"])
            di.enable_diagnosis = value
            applied["enable_diagnosis"] = value
        if args.get("show_io_warnings_as_errors") is not None:
            value = bool(args["show_io_warnings_as_errors"])
            di.show_io_warnings_as_errors = value
            applied["show_io_warnings_as_errors"] = value
        if not applied:
            raise RuntimeError("device_info driver_info_set needs at "
                               "least one of bus_cycle_task, "
                               "io_application, update_ios_while_in_stop, "
                               "behaviour_for_outputs_on_stop, "
                               "user_program_for_stop_reset_behaviour, "
                               "always_update_variables, "
                               "generate_force_variables, "
                               "enable_diagnosis, "
                               "show_io_warnings_as_errors")
        log("OK", "driver info set: {a}".format(a=sorted(applied)))
        return {"device": _device_label(dev), "applied": applied,
                "driver_info": _driver_info_dict(di)}
    raise RuntimeError('device_info needs args.op of "identification", '
                       '"simulation", "simulation_set", "driver_info" or '
                       '"driver_info_set"')


def _param_elements(param):
    """The data elements of one ScriptDeviceParameter.

    LIVE 2026-10-22: iterating the parameter's element collection raises
    the .NET positional-index error on this build - the parameter ITSELF
    is a ScriptDataElement, so it doubles as the single-element
    fallback."""
    try:
        elements = list(param)
    except Exception:
        elements = None
    if not elements:
        return [param]
    return elements


def cmd_device_params(project, args):
    """Device parameter introspection and online read/write
    (ScriptDeviceParameters stub).

    ops:
      list  {device?, connector_id?} -> parameter inventory
      read  {device?, name?|id?, online?, timeout?} -> parameter value
            (online=true reads from the PLC)
      write {device?, name?|id?, value, element?, confirm} -> ONLINE
            PARAMETER WRITE (CONFIRM-GATED, PLC-touching); enums are
            validated against allowed values
    """
    op = args.get("op", "list")
    dev = _device_obj(project, args)
    params = dev.device_parameters
    connector_id = args.get("connector_id")
    if connector_id is not None:
        params = dev.connectors.by_id(int(connector_id)).host_parameters

    def _find_param():
        pname = args.get("name", "")
        pid = args.get("id")
        if pid is not None:
            return params.by_id(int(pid))
        if pname:
            # LIVE 2026-10-22: params[pname] feeds the string into the
            # .NET POSITIONAL indexer ("Index was out of range ... index")
            # - enumerate and match on name instead (same lesson as the
            # library-manager lookups).
            target = str(pname)
            lower = target.lower()
            fallback = None
            for p in params:
                try:
                    p_name = str(p.name)
                except Exception:
                    continue
                if p_name == target:
                    return p
                if fallback is None and p_name.lower() == lower:
                    fallback = p
            if fallback is not None:
                return fallback
            raise RuntimeError('no parameter named "{n}"'.format(n=target))
        raise RuntimeError("device_params {o} needs args.name or "
                           "args.id".format(o=op))

    def _element_summary(el, online, timeout):
        entry = {}
        try:
            entry["identifier"] = str(el.identifier)
        except Exception:
            try:
                entry["identifier"] = str(el.name)
            except Exception:
                entry["identifier"] = None
        try:
            entry["visible_name"] = str(el.visible_name)
        except Exception:
            pass
        for attr in ("unit", "description", "iec_type"):
            try:
                entry[attr] = str(getattr(el, attr))
            except Exception:
                entry[attr] = None
        try:
            entry["can_access_online"] = bool(el.can_access_online)
        except Exception:
            entry["can_access_online"] = None
        if online and entry["can_access_online"]:
            try:
                entry["online_value"] = str(
                    el.read_online_value(int(timeout or 1000)))
            except Exception as exc:
                entry["online_value"] = "error: {e}".format(
                    e=str(exc)[:120])
        else:
            try:
                entry["value"] = str(el.value)
            except Exception:
                entry["value"] = None
        try:
            if el.is_enumeration:
                entry["allowed_values"] = [
                    str(v.identifier) for v in el.allowed_values]
        except Exception:
            pass
        try:
            if el.is_range_type:
                entry["min_value"] = str(el.min_value)
                entry["max_value"] = str(el.max_value)
        except Exception:
            pass
        try:
            if el.has_sub_elements:
                subs = []
                for sub in el:
                    subs.append(_element_summary(sub, False, timeout))
                entry["sub_elements"] = subs
        except Exception:
            pass
        return entry

    if op == "list":
        out = []
        for param in params:
            entry = {}
            for attr in ("id", "name", "type_string", "section",
                         "iec_type", "param_type", "channel_type"):
                try:
                    entry[attr] = str(getattr(param, attr))
                except Exception:
                    entry[attr] = None
            out.append(entry)
        log("OK", "device params: {n}".format(n=len(out)))
        return {"device": _device_label(dev), "count": len(out),
                "parameters": out}

    if op == "read":
        param = _find_param()
        online_read = bool(args.get("online", False))
        timeout = args.get("timeout", 1000)
        entry = {"id": str(param.id), "name": str(param.name)}
        for attr in ("type_string", "iec_type"):
            try:
                entry[attr] = str(getattr(param, attr))
            except Exception:
                entry[attr] = None
        for el in _param_elements(param):
            entry.setdefault("elements", []).append(
                _element_summary(el, online_read, timeout))
        log("OK", "param {n}: read ({o})".format(
            n=param.name, o="online" if online_read else "offline"))
        return entry

    if op == "write":
        if not args.get("confirm"):
            raise RuntimeError("device_params write REFUSED - requires "
                               "args.confirm=true (it writes a parameter "
                               "to the DEVICE)")
        param = _find_param()
        value = args.get("value")
        if value is None:
            raise RuntimeError("device_params write needs args.value")
        timeout = args.get("timeout", 1000)
        target = str(args.get("element", "")).strip().lower()
        results = []
        for el in _param_elements(param):
            if target and str(el.identifier).lower() != target:
                continue
            try:
                if el.is_enumeration:
                    match = None
                    for allowed in el.allowed_values:
                        if str(allowed.identifier) == str(value):
                            match = allowed
                            break
                    if match is None:
                        raise RuntimeError(
                            "value '{v}' not in allowed values {a}".format(
                                v=value,
                                a=[str(v.identifier)
                                   for v in el.allowed_values]))
                    el.write_online_value(match)
                else:
                    el.write_online_value(str(value))
                try:
                    readback = str(el.read_online_value(int(timeout)))
                except Exception:
                    readback = None
                results.append({"identifier": str(el.identifier),
                                "written": str(value),
                                "readback": readback})
            except RuntimeError:
                raise
            except Exception as exc:
                results.append({"identifier": str(el.identifier),
                                "error": str(exc)[:150]})
        ok = bool(results) and all("error" not in r for r in results)
        log("OK" if ok else "ERROR",
            "param write {n}: {r}".format(n=param.name, r=results))
        if not results:
            raise RuntimeError("no parameter element matched "
                               "'{t}'".format(t=target or "<any>"))
        return {"parameter": str(param.name), "ok": ok,
                "elements": results}
    raise RuntimeError('device_params needs args.op of "list", "read" or '
                       '"write"')


def _mapping_sets(dev, connector_id):
    """(label, parameter set) pairs to walk for io mappings: the device
    parameter set plus every connector's host_parameters - or just the
    one named connector (ScriptDeviceParameters stub: mappings live on
    ScriptDataElement.io_mapping, 3.5.8.0)."""
    pairs = [("device", dev.device_parameters)]
    if connector_id is not None:
        conn = dev.connectors.by_id(int(connector_id))
        return [("connector {c}".format(c=connector_id),
                 conn.host_parameters)]
    try:
        for conn in dev.connectors:
            try:
                cid = str(conn.id)
            except Exception:
                cid = "?"
            try:
                pairs.append(("connector {c}".format(c=cid),
                              conn.host_parameters))
            except Exception:
                continue
    except Exception:
        pass
    return pairs


def _device_label(dev):
    """Readable device name: get_name() first (tree name, e.g.
    'PFC200_2ETH_RS'), then the 'Name=<x>' repr slice, then the repr."""
    try:
        name = str(dev.get_name())
        if name:
            return name
    except Exception:
        pass
    name = _short_name(dev)
    if name.startswith("Name="):
        return name[len("Name="):]
    return name


def _mapping_summary(el):
    """One element -> mapping summary dict, or None when it carries no
    mapping and is not mappable (ScriptDataElement.io_mapping is the
    primary signal; is_mappable_io the fallback - LIVE 2026-10-22: the
    engine's own CSV export sees mappings that is_mappable_io-gated
    walks miss, so io_mapping is probed on every element)."""
    try:
        entry_id = str(el.identifier)
    except Exception:
        return None
    try:
        m = el.io_mapping
    except Exception:
        m = None
    if m is None:
        try:
            if not el.is_mappable_io:
                return None
        except Exception:
            return None
        return {"identifier": entry_id, "io_mapping": None}
    entry = {"identifier": entry_id}
    try:
        entry["visible_name"] = str(el.visible_name)
    except Exception:
        pass
    md = {}
    for name, reader in (
            ("id", lambda: str(m.Id)),
            ("variable", lambda: str(m.variable)),
            ("default_variable", lambda: str(m.default_variable)),
            ("mapping_creates_variable",
             lambda: bool(m.mapping_creates_variable)),
            ("maps_to_existing_variable",
             lambda: bool(m.maps_to_existing_variable)),
            ("automatic_iec_address",
             lambda: bool(m.automatic_iec_address)),
            ("manual_iec_address", lambda: str(m.manual_iec_address)),
    ):
        try:
            md[name] = reader()
        except Exception:
            md[name] = None
    entry["io_mapping"] = md
    return entry


def _walk_mapping_elements(el, out):
    """Depth-first collection of mappable elements (struct parameters
    carry their mappings on sub-elements)."""
    entry = _mapping_summary(el)
    if entry is not None:
        out.append(entry)
    try:
        if el.has_sub_elements:
            for sub in el:
                _walk_mapping_elements(sub, out)
    except Exception:
        pass


def _walkable_devices(project, args):
    """Devices to walk for io mappings: the named device, or every
    is_device object in the project tree (LIVE 2026-10-22: the mapped
    channels live on the plugged modules below the root device - a
    root-only walk found nothing while the engine's own CSV export
    listed them)."""
    name = str(args.get("device", "")).strip()
    if name:
        return [_device_obj(project, args)]
    devs = []
    try:
        for obj in project.get_children(True):
            try:
                if obj.is_device:
                    devs.append(obj)
            except Exception:
                continue
    except Exception:
        pass
    if not devs:
        devs = [get_device(project, "")]
    return devs


def cmd_device_io(project, args):
    """IO mapping inspection and CSV round-trip
    (ScriptDeviceObject.export/import_io_mappings_as_csv, 3.5.8.0).

    ops:
      mappings    {device?, connector_id?} -> every mappable parameter
                  element with its io_mapping (variable, default_variable,
                  mapping_creates_variable, maps_to_existing_variable,
                  automatic_iec_address, manual_iec_address) - read-only
      set_mapping {device?, connector_id?, name?|id?, element?,
                  variable?, manual_iec_address?, automatic?,
                  all_elements?, confirm}
                  -> set mapping.variable and/or manual_iec_address
                  (automatic=true resets to the automatic address;
                  CONFIRM-GATED - modifies the IO configuration).
                  Multi-element parameters REFUSE without element=
                  <identifier> unless all_elements=true (G-21)
      export_csv  {device?, file?} -> export_io_mappings_as_csv (default
                  <ECP_PROJECT_ROOT>/io-mappings/<device>_io_mappings.csv)
      import_csv  {device?, file, confirm} -> import_io_mappings_from_csv
                  (CONFIRM-GATED - modifies the IO configuration)
    """
    op = args.get("op", "mappings")
    dev = _device_obj(project, args)

    if op == "mappings":
        out = []
        for wdev in _walkable_devices(project, args):
            dname = _device_label(wdev)
            try:
                sets = _mapping_sets(wdev, args.get("connector_id"))
            except Exception:
                continue
            for label, params in sets:
                for param in params:
                    try:
                        pname = str(param.name)
                    except Exception:
                        pname = "?"
                    entries = []
                    try:
                        for el in param:
                            _walk_mapping_elements(el, entries)
                    except Exception:
                        continue
                    for e in entries:
                        e["parameter"] = pname
                        e["source"] = label
                        e["device"] = dname
                    out.extend(entries)
        log("OK", "io mappings: {n} mappable element(s)".format(n=len(out)))
        return {"device": _device_label(dev), "count": len(out),
                "mappings": out}

    if op == "set_mapping":
        if not args.get("confirm"):
            raise RuntimeError("device_io set_mapping REFUSED - requires "
                               "args.confirm=true (it modifies the IO "
                               "configuration)")
        variable = args.get("variable")
        set_auto = bool(args.get("automatic", False))
        manual = args.get("manual_iec_address")
        if variable is None and not set_auto and manual is None:
            raise RuntimeError("device_io set_mapping needs args.variable "
                               "and/or args.manual_iec_address / "
                               "args.automatic")
        pname = args.get("name", "")
        pid = args.get("id")
        target_name = str(pname).lower() if pname else ""
        for label, params in _mapping_sets(dev, args.get("connector_id")):
            param = None
            try:
                if pid is not None:
                    param = params.by_id(int(pid))
                elif target_name:
                    # enumerate - params[name] hits the .NET positional
                    # indexer live (2026-10-22, see device_params)
                    for p in params:
                        try:
                            if str(p.name).lower() == target_name:
                                param = p
                                break
                        except Exception:
                            continue
                else:
                    continue
            except Exception:
                continue
            if param is not None:
                break
        else:
            raise RuntimeError("device_io set_mapping: no parameter "
                               "matched args.name/args.id")
        target = str(args.get("element", "")).strip().lower()
        allow_all = bool(args.get("all_elements", False))
        cands = []
        for el in _param_elements(param):
            if target and str(el.identifier).lower() != target:
                continue
            try:
                m = el.io_mapping
            except Exception:
                m = None
            if m is None:
                continue
            cands.append((el, m))
        # LIVE 2026-10-22 (G-21): an unfiltered set on a multi-channel
        # parameter rewrote ALL channel variables (16 xBtn* clobbered,
        # repaired via the CSV round-trip) - require an explicit element
        # unless the caller deliberately asks for every element.
        if len(cands) > 1 and not target and not allow_all:
            raise RuntimeError(
                "set_mapping REFUSED - parameter '{p}' has {n} mappable "
                "elements (e.g. {i}); pass element=<identifier> to set "
                "exactly one, or all_elements=true to deliberately "
                "apply to all".format(
                    p=str(param.name), n=len(cands),
                    i=", ".join(str(el.identifier)
                                for el, _ in cands[:4])))
        results = []
        for el, m in cands:
            touched = {}
            if variable is not None:
                m.variable = str(variable)
                touched["variable"] = str(variable)
            if set_auto:
                m.manual_iec_address = None
                touched["manual_iec_address"] = None
            elif manual is not None:
                m.manual_iec_address = str(manual)
                touched["manual_iec_address"] = str(manual)
            readback = {}
            for name, reader in (
                    ("variable", lambda: str(m.variable)),
                    ("automatic_iec_address",
                     lambda: bool(m.automatic_iec_address)),
                    ("manual_iec_address",
                     lambda: str(m.manual_iec_address))):
                try:
                    readback[name] = reader()
                except Exception:
                    readback[name] = None
            results.append({"identifier": str(el.identifier),
                            "set": touched, "readback": readback})
        if not results:
            raise RuntimeError("no mappable element matched "
                               "'{t}'".format(t=target or "<any>"))
        ok = all(r["readback"].get("variable") is not None
                 for r in results)
        log("OK" if ok else "WARN",
            "mapping set on {n} element(s): {u}".format(
                n=len(results), u=sorted(
                    k for r in results for k in r["set"])))
        return {"parameter": str(param.name), "ok": ok,
                "elements": results}

    if op == "export_csv":
        path = str(args.get("file", "") or "").strip()
        if not path:
            base = PROJECT_ROOT or WORK_DIR
            if not base:
                raise RuntimeError("device_io export_csv needs args.file "
                                   "(or ECP_PROJECT_ROOT / ECP_WORK_DIR "
                                   "for the default location)")
            target_dir = os.path.join(base, "io-mappings")
            if not os.path.isdir(target_dir):
                os.makedirs(target_dir)
            sane = _device_label(dev)
            if sane.startswith("Name="):
                sane = sane[len("Name="):]
            sane = re.sub(r"[^A-Za-z0-9_.-]+", "_", sane).strip("_")
            path = os.path.join(
                target_dir, "{d}_io_mappings.csv".format(d=sane or "device"))
        path = os.path.abspath(path)
        parent = os.path.dirname(path)
        if parent and not os.path.isdir(parent):
            os.makedirs(parent)
        dev.export_io_mappings_as_csv(path)
        if not os.path.isfile(path):
            raise RuntimeError("export_io_mappings_as_csv reported "
                               "success but wrote no file: {p}".format(
                                   p=path))
        size = os.path.getsize(path)
        log("OK", "io mappings exported: {p} ({n} bytes)".format(
            p=path, n=size))
        return {"device": _device_label(dev), "file": path, "bytes": size}

    if op == "import_csv":
        if not args.get("confirm"):
            raise RuntimeError("device_io import_csv REFUSED - requires "
                               "args.confirm=true (it modifies the IO "
                               "configuration)")
        path = str(args.get("file", "") or "").strip()
        if not path or not os.path.isfile(path):
            raise RuntimeError("device_io import_csv needs args.file of "
                               "an existing CSV (got '{p}')".format(p=path))
        dev.import_io_mappings_from_csv(os.path.abspath(path))
        log("OK", "io mappings imported from {p}".format(p=path))
        return {"device": _device_label(dev), "file": os.path.abspath(path),
                "imported": True}
    raise RuntimeError('device_io needs args.op of "mappings", '
                       '"set_mapping", "export_csv" or "import_csv"')


def cmd_app_control(project, args):
    """Application status / activate / offline boot application / start /
    reset (ScriptApplication + ScriptOnlineApplication stubs).

    ops:
      status  {application?} -> is_active, is_uptodate,
              is_online_change_possible (read-only; 3.5.10.0 fields)
      activate {application?, confirm} -> set project.active_application
      boot    {application?, path, confirm} -> OFFLINE boot application
              file on this host (create_boot_application; NOT a PLC
              download; update_compile_info stays False to preserve
              online-change compatibility)
      start   {application?, confirm} -> online application start
              (PLC-touching; requires login)
      reset   {application?, option?: warm|cold|original, force_kill?,
              confirm} -> DESTRUCTIVE: PLC reset; 'original' erases
              applications, boot project and retain data
    """
    op = args.get("op", "status")
    app = _get_application_obj(project, args)

    if op == "status":
        out = {"application": _short_name(app)}
        for attr, key in (("is_active_application", "is_active"),
                          ("is_uptodate", "is_uptodate"),
                          ("is_online_change_possible",
                           "is_online_change_possible")):
            try:
                out[key] = bool(getattr(app, attr))
            except Exception:
                out[key] = None
        log("OK", "app status: {s}".format(s=out))
        return out

    if op == "activate":
        if not args.get("confirm"):
            raise RuntimeError("app activate REFUSED - requires "
                               "args.confirm=true (it changes which "
                               "application is the active one)")
        project.active_application = app
        log("OK", "active application set to {n}".format(
            n=_short_name(app)))
        return {"activated": _short_name(app)}

    if op == "boot":
        if not args.get("confirm"):
            raise RuntimeError("app boot REFUSED - requires "
                               "args.confirm=true (it writes a boot "
                               "application file)")
        path = str(args.get("path", "")).strip()
        if not path:
            raise RuntimeError("app boot needs args.path (host .bootapp "
                               "file path)")
        d = os.path.dirname(path)
        if d and not os.path.isdir(d):
            os.makedirs(d)
        app.create_boot_application(path)
        log("OK", "offline boot application written to {p}".format(p=path))
        return {"bootapp": path,
                "bytes": os.path.getsize(path) if
                os.path.isfile(path) else None}

    if op == "start":
        if not args.get("confirm"):
            raise RuntimeError("app start REFUSED - requires "
                               "args.confirm=true (it STARTS the program "
                               "on the RUNNING PLC)")
        user, pw = _require_creds()
        online.set_default_credentials(user, pw)
        onlineapp = online.create_online_application(app)
        onlineapp.start()
        log("OK", "application started on the PLC")
        return {"started": True}

    if op == "reset":
        if not args.get("confirm"):
            raise RuntimeError("app reset REFUSED - requires "
                               "args.confirm=true. DESTRUCTIVE: resets "
                               "the PLC; option 'original' deletes ALL "
                               "applications, boot project and retain "
                               "data.")
        option_map = {"warm": "Warm", "cold": "Cold", "original": "Original"}
        option = str(args.get("option", "warm")).lower()
        if ResetOption is None:
            raise RuntimeError("ResetOption is unavailable outside "
                               "e!COCKPIT")
        if option not in option_map:
            raise RuntimeError("app reset needs args.option of warm, cold "
                               "or original")
        user, pw = _require_creds()
        online.set_default_credentials(user, pw)
        onlineapp = online.create_online_application(app)
        try:
            reset_option = getattr(ResetOption, option_map[option])
            onlineapp.reset(reset_option,
                            bool(args.get("force_kill", False)))
        except AttributeError as exc:
            raise RuntimeError("ResetOption.{m} unavailable: {e}".format(
                m=option_map[option], e=exc))
        log("OK", "PLC reset done ({o})".format(o=option))
        return {"reset": option, "force_kill":
                bool(args.get("force_kill", False))}
    raise RuntimeError('app_control needs args.op of "status", '
                       '"activate", "boot", "start" or "reset"')


def _projects_global():
    """The projects root (IScriptProjects): this live build injects
    e_projects; the stubs document `projects`. Resolve whichever
    exists."""
    for name in ("e_projects", "projects"):
        if name in globals() and globals()[name] is not None:
            return globals()[name]
    raise RuntimeError("projects root unavailable outside e!COCKPIT")


def _write_active_project(entry):
    """Atomically persist the op=use sidecar (same tmp+rename pattern as
    the PLC-state sidecar: a crash mid-write never leaves a half file
    behind)."""
    tmp = ACTIVE_PROJECT_FILE + ".tmp"
    with open(tmp, "wb") as fh:
        fh.write(json.dumps(entry).encode("utf-8", "replace"))
    try:
        os.remove(ACTIVE_PROJECT_FILE)
    except OSError:
        pass
    os.rename(tmp, ACTIVE_PROJECT_FILE)


def cmd_project_control(project, args):
    """Project lifecycle + info (ScriptProjects/ScriptProject stubs).

    ops:
      info      -> project info object fields (read-only)
      info_set  {company?/title?/version?/author?/description?/
                 default_namespace?, confirm} -> sets project info fields
      save      -> saves the open project (its physical location)
      create    {path, confirm} -> NEW project at path (becomes primary)
      open      {path, confirm} -> opens a project (becomes primary;
                update_flags = silent + update all)
      open_archive {archive, project_path, overwrite?, confirm} ->
                restores a project archive
      close     {confirm} -> DESTRUCTIVE: closes the project, DISCARDS
                unsaved changes
      use       {root, open?, ecp?, confirm} -> SEQUENTIAL project/
                worktree switch: validates the triplet {root exists,
                Application.*.xml under plcopensrc/, exactly one .ecp
                (args.ecp picks when several)}, optionally opens the .ecp
                as primary, persists it to the active-project sidecar.
                The sidecar OVERRIDES the .env's ECP_PROJECT_ROOT/
                ECP_XML_DIR for every later command (the relay re-execs
                the executor per command - no restart, no re-arm).
      use_clear {confirm} -> removes the sidecar - back to the .env config
    create/open/open_archive/close swap the GUI's primary project -
    treat them as session-mutating and always confirm-gated. use/use_clear
    are confirm-gated too: use may open a project AND repoints every
    later build's import sources.
    """
    op = args.get("op", "info")

    if op == "info":
        out = {"path": str(project.path) if project.path else None,
               "dirty": bool(project.dirty)}
        try:
            info = project.get_project_info()
            for attr in ("company", "title", "author", "description",
                         "default_namespace", "released"):
                try:
                    out[attr] = str(getattr(info, attr))
                except Exception:
                    out[attr] = None
            try:
                out["version"] = str(info.version)
            except Exception:
                out["version"] = None
        except Exception as exc:
            out["project_info"] = "unavailable: {e}".format(
                e=str(exc)[:120])
        log("OK", "project info read")
        return out

    if op == "info_set":
        if not args.get("confirm"):
            raise RuntimeError("project info_set REFUSED - requires "
                               "args.confirm=true (it modifies project "
                               "metadata)")
        info = project.get_project_info()
        changed = []
        for attr in ("company", "title", "author", "description",
                     "default_namespace"):
            if attr in args:
                setattr(info, attr, str(args[attr]))
                changed.append(attr)
        if "version" in args:
            info.version = str(args["version"])
            changed.append("version")
        log("OK", "project info updated: {c}".format(c=changed))
        return {"updated": changed}

    if op == "save":
        project.save()
        log("OK", "project saved")
        return {"saved": True, "path": str(project.path)}

    if op == "create":
        if not args.get("confirm"):
            raise RuntimeError("project create REFUSED - requires "
                               "args.confirm=true (it creates a project "
                               "and makes it the primary one)")
        path = str(args.get("path", "")).strip()
        if not path:
            raise RuntimeError("project create needs args.path")
        _projects_global().create(path, True)
        log("OK", "project created at {p} (primary)".format(p=path))
        return {"created": path, "primary": True}

    if op == "open":
        if not args.get("confirm"):
            raise RuntimeError("project open REFUSED - requires "
                               "args.confirm=true (it opens a project "
                               "and makes it the primary one; unsaved "
                               "changes in other projects are the "
                               "GUI's responsibility)")
        path = str(args.get("path", "")).strip()
        if not path:
            raise RuntimeError("project open needs args.path")
        opened = _projects_global().open(path, True)
        log("OK", "project opened: {p}".format(p=opened.path))
        return {"opened": str(opened.path), "primary": True}

    if op == "open_archive":
        if not args.get("confirm"):
            raise RuntimeError("project open_archive REFUSED - requires "
                               "args.confirm=true (it restores a project "
                               "archive and opens it)")
        archive = str(args.get("archive", "")).strip()
        project_path = str(args.get("project_path", "")).strip()
        if not archive or not project_path:
            raise RuntimeError("project open_archive needs args.archive "
                               "and args.project_path")
        opened = _projects_global().open_archive(
            archive, project_path, bool(args.get("overwrite", False)))
        log("OK", "archive restored to {p}".format(p=opened.path))
        return {"opened": str(opened.path), "from_archive": archive}

    if op == "close":
        if not args.get("confirm"):
            raise RuntimeError("project close REFUSED - requires "
                               "args.confirm=true. DESTRUCTIVE: closes "
                               "the project and DISCARDS unsaved "
                               "changes.")
        project.close()
        log("OK", "project closed")
        return {"closed": True}

    if op == "use":
        if not args.get("confirm"):
            raise RuntimeError("project use REFUSED - requires "
                               "args.confirm=true (it switches the "
                               "executor's active project/worktree: "
                               "every later build imports from the new "
                               "root and may open the new .ecp as "
                               "primary)")
        root = str(args.get("root", "")).strip()
        if not root:
            raise RuntimeError("project use needs args.root")
        root = os.path.abspath(root)
        if not os.path.isdir(root):
            raise RuntimeError(
                "project use REFUSED - root does not exist: {r}. Create "
                "the checkout/worktree first, then retry "
                "(the switch is refused, NOTHING was written or "
                "opened).".format(r=root))
        xml_dir = discover_xml_dir(root)
        if not xml_dir:
            raise RuntimeError(
                "project use REFUSED - no Application.*.xml found under "
                "{r}\\plcopensrc (directly or one level deep); a root "
                "without exportable XML sources is not a usable "
                "automation project".format(r=root))
        ecp_arg = str(args.get("ecp", "")).strip()
        if ecp_arg:
            ecp = ecp_arg if os.path.isabs(ecp_arg) \
                else os.path.join(root, ecp_arg)
            ecp = os.path.abspath(ecp)
            if not os.path.isfile(ecp):
                raise RuntimeError(
                    "project use REFUSED - args.ecp not found: {e}".format(
                        e=ecp))
        else:
            candidates = _ecp_candidates(root)
            if len(candidates) > 1:
                raise RuntimeError(
                    "project use REFUSED - multiple .ecp candidates "
                    "under {r}: pass args.ecp with one of: {c}".format(
                        r=root,
                        c=", ".join(os.path.basename(h)
                                    for h in candidates)))
            ecp = os.path.abspath(candidates[0]) if candidates else ""
        opened = None
        if args.get("open", True):
            if ecp:
                opened = _projects_global().open(ecp, True)
                log("OK", "project opened: {p}".format(p=opened.path))
            else:
                log("INFO", "no .ecp under the new root - the "
                            "already-open primary project stays (pass "
                            "args.ecp to open a specific one)")
        entry = {"project_root": root, "xml_dir": xml_dir, "ecp": ecp,
                 "set_at": time.time(), "source": "op=use"}
        _write_active_project(entry)
        log("OK", "active project switched (op=use): root={r} xml={x} "
                  "ecp={e} - overrides .env until use_clear".format(
                      r=root, x=xml_dir, e=ecp or "<none>"))
        return {"project_root": root, "xml_dir": xml_dir, "ecp": ecp,
                "opened": str(opened.path) if opened is not None else None,
                "sidecar": ACTIVE_PROJECT_FILE}

    if op == "use_clear":
        if not args.get("confirm"):
            raise RuntimeError("project use_clear REFUSED - requires "
                               "args.confirm=true (it reverts the "
                               "executor to the .env project config)")
        removed = False
        try:
            os.remove(ACTIVE_PROJECT_FILE)
            removed = True
        except OSError:
            pass
        log("OK", "active-project sidecar cleared ({r}) - back to the "
                  ".env config".format(
                      r=ACTIVE_PROJECT_FILE if removed else "was absent"))
        return {"cleared": removed,
                "project_root": _cfg("ECP_PROJECT_ROOT", "") or None}

    raise RuntimeError('project_control needs args.op of "info", '
                       '"info_set", "save", "create", "open", '
                       '"open_archive", "close", "use" or "use_clear"')


# --------------------------------------------------------------------------
# Command dispatch
# --------------------------------------------------------------------------
KNOWN_COMMANDS = ("build", "import", "compile", "rebuild", "status", "read",
                  "write", "unforce", "download", "disconnect", "stop",
                  "state", "probe", "messages", "snapshot", "export",
                  "plcfs", "upload_source", "save_archive",
                  "clear_messages", "system",
                  "libs", "lib_find", "lib_install", "lib_uninstall",
                  "repos", "repo_add", "repo_remove", "repo_update",
                  "repo_move",
                  "login", "source_download", "prepared_values",
                  "tree", "object_create", "textual", "task_config",
                  "symbol_config", "device_scan", "device_comm",
                  "device_info", "device_params", "device_io",
                  "app_control", "project_control")


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
    _headless_hardening()
    log("INFO", "command '{c}' (id {i}) starting".format(c=cmd, i=rid or "<direct>"))
    # A live kept login makes the engine REFUSE project-tree mutations
    # ("affects a device you are currently logged into") - covered
    # commands drop the session here, before any engine call.
    _logout_kept_if_mutation(cmd, args)

    try:
        if cmd in ("build", "import", "compile", "rebuild"):
            project = get_project()
            app = get_application(project)
            _warn_primary_mismatch(project)
            if cmd in ("build", "import") and not (args.get("skip_import") or SKIP_IMPORT):
                import_xml_files(app, summary)
                cleanup_removed_objects(project, summary)
                # Let e!COCKPIT settle the project tree after imports.
                system.delay(500)
            else:
                log("INFO", "import step skipped for '{c}'".format(c=cmd))
            if cmd in ("build", "compile", "rebuild"):
                compile_project(app, project, summary, args,
                                rebuild=(cmd == "rebuild"))
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
        elif cmd == "write":
            project = get_project()
            result["value"] = cmd_write(project, args)
            summary["ok"] = True
        elif cmd == "unforce":
            project = get_project()
            result["value"] = cmd_unforce(project, args)
            summary["ok"] = True
        elif cmd == "download":
            project = get_project()
            cmd_download(project, args)
            summary["ok"] = True
        elif cmd == "disconnect":
            project = get_project()
            cmd_disconnect(project, args)
            summary["ok"] = True
        elif cmd == "stop":
            project = get_project()
            result["value"] = cmd_stop(project, args)
            summary["ok"] = True
        elif cmd == "state":
            project = get_project()
            result["value"] = cmd_state(project, args)
            summary["ok"] = True
        elif cmd == "probe":
            project = get_project()
            cmd_probe(project)
            summary["ok"] = True
        elif cmd == "messages":
            # System-level: no project lookup needed.
            result["value"] = cmd_messages()
            summary["ok"] = True
        elif cmd == "system":
            # System-level: the injected `system` object; no project.
            result["value"] = cmd_system(args)
            summary["ok"] = True
        elif cmd == "snapshot":
            project = get_project()
            result["value"] = cmd_snapshot(project, args)
            summary["ok"] = True
        elif cmd == "export":
            project = get_project()
            result["value"] = cmd_export(project, args, summary)
            summary["ok"] = True
        elif cmd == "plcfs":
            project = get_project()
            result["value"] = cmd_plcfs(project, args)
            summary["ok"] = True
        elif cmd == "upload_source":
            project = get_project()
            result["value"] = cmd_upload_source(project, args)
            summary["ok"] = True
        elif cmd == "save_archive":
            project = get_project()
            result["value"] = cmd_save_archive(project, args)
            summary["ok"] = True
        elif cmd == "clear_messages":
            # System-level: wipe the message store (fresh-harvest aid).
            # clear_messages takes ONE category Guid - iterate them all.
            cleared = 0
            failed = 0
            for cat in system.get_message_categories():
                try:
                    system.clear_messages(cat)
                    cleared += 1
                except Exception as exc:
                    failed += 1
                    log("WARN", "clear_messages({c}) failed: {e}".format(
                        c=str(cat)[:8], e=exc))
            log("OK", "message store cleared ({n} categories, {f} failed)"
                .format(n=cleared, f=failed))
            result["value"] = {"categories_cleared": cleared,
                               "categories_failed": failed}
            summary["ok"] = True
        elif cmd == "libs":
            # Library curation: all system-level (global `librarymanager`),
            # no project lookup needed.
            result["value"] = cmd_libs(args)
            summary["ok"] = True
        elif cmd == "lib_find":
            result["value"] = cmd_lib_find(args)
            summary["ok"] = True
        elif cmd == "lib_install":
            result["value"] = cmd_lib_install(args)
            summary["ok"] = True
        elif cmd == "lib_uninstall":
            result["value"] = cmd_lib_uninstall(args)
            summary["ok"] = True
        elif cmd == "repos":
            result["value"] = cmd_repos(args)
            summary["ok"] = True
        elif cmd == "repo_add":
            result["value"] = cmd_repo_add(args)
            summary["ok"] = True
        elif cmd == "repo_remove":
            result["value"] = cmd_repo_remove(args)
            summary["ok"] = True
        elif cmd == "repo_update":
            result["value"] = cmd_repo_update(args)
            summary["ok"] = True
        elif cmd == "repo_move":
            result["value"] = cmd_repo_move(args)
            summary["ok"] = True
        elif cmd == "login":
            # Online-change control: login and STAY connected.
            project = get_project()
            result["value"] = cmd_login(project, args)
            summary["ok"] = True
        elif cmd == "source_download":
            project = get_project()
            result["value"] = cmd_source_download(project, args)
            summary["ok"] = True
        elif cmd == "prepared_values":
            project = get_project()
            result["value"] = cmd_prepared_values(project, args)
            summary["ok"] = True
        elif cmd == "tree":
            project = get_project()
            result["value"] = cmd_tree(project, args)
            summary["ok"] = True
        elif cmd == "symbol_config":
            project = get_project()
            result["value"] = cmd_symbol_config(project, args)
            summary["ok"] = True
        elif cmd == "device_scan":
            project = get_project()
            result["value"] = cmd_device_scan(project, args)
            summary["ok"] = True
        elif cmd == "device_comm":
            project = get_project()
            result["value"] = cmd_device_comm(project, args)
            summary["ok"] = True
        elif cmd == "device_info":
            project = get_project()
            result["value"] = cmd_device_info(project, args)
            summary["ok"] = True
        elif cmd == "device_params":
            project = get_project()
            result["value"] = cmd_device_params(project, args)
            summary["ok"] = True
        elif cmd == "device_io":
            project = get_project()
            result["value"] = cmd_device_io(project, args)
            summary["ok"] = True
        elif cmd == "app_control":
            project = get_project()
            result["value"] = cmd_app_control(project, args)
            summary["ok"] = True
        elif cmd == "project_control":
            project = get_project()
            result["value"] = cmd_project_control(project, args)
            summary["ok"] = True
        elif cmd == "task_config":
            project = get_project()
            result["value"] = cmd_task_config(project, args)
            summary["ok"] = True
        elif cmd == "object_create":
            project = get_project()
            result["value"] = cmd_object_create(project, args)
            summary["ok"] = True
        elif cmd == "textual":
            project = get_project()
            result["value"] = cmd_textual(project, args)
            summary["ok"] = True
        else:
            raise RuntimeError("unknown command '{c}' (known: {k})".format(
                c=cmd, k=", ".join(KNOWN_COMMANDS)))
    except Exception:
        summary["ok"] = False
        summary["errors"] += 1
        result["error"] = traceback.format_exc()
        # Safety net for mutators not covered by TREE_MUTATION_SPECS:
        # name the fix instead of a bare engine refusal.
        if "currently logged into" in result["error"]:
            hint = (
                "HINT: the engine refuses project mutations while a login "
                "is live. The kept keep-login session should have been "
                "dropped automatically for this command - if it was NOT "
                "(uncovered mutator), call ecockpit_disconnect and retry; "
                "the mutation then runs on a clean project.")
            result["error"] = result["error"] + "\n" + hint
            log("WARN", "live-login refusal on '{c}' - mutation hint "
                "appended".format(c=cmd))
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
# The command relay instead sets ECP_NO_RUN=1 and calls run_command()
# itself once per request.
if not os.environ.get("ECP_NO_RUN"):
    run_command({"id": "", "cmd": "build", "args": {}})
