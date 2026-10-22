# -*- coding: utf-8 -*-
"""Offline unit tests for the ecockpit-mcp executor commands
(library curation, export reporter, save_archive ladder).

The executor talks to the ScriptEngine-injected `librarymanager` global
(ILibManager). Here a plain-Python fake following the ScriptEngine.chm
signatures is injected into the module globals, so the command logic
(scoping, refusals, ambiguity handling, argument quirks) is testable
without e!COCKPIT.
"""

import os

import pytest

os.environ["ECP_NO_RUN"] = "1"  # executor's import-time auto-build

from ecockpit_mcp import executor as ex  # noqa: E402


class FakeRepo(object):
    def __init__(self, name, root, editable=True):
        self.name = name
        self.root_folder = root
        self.editable = editable
        self.libs = {}  # displayname -> FakeLib

    def __repr__(self):
        return "FakeRepo({})".format(self.name)


class FakeLib(object):
    def __init__(self, name, displayname=None, version="1.0.0.0",
                 company="WAGO", namespace="", title="", dependencies=None):
        self.name = name
        self.displayname = displayname if displayname is not None else name
        self.version = version
        self.company = company
        self.default_namespace = namespace
        self.title = title
        self.dependencies = dependencies or []


class FakeLibManager(object):
    """ILibManager per the LIVE BUILD (verified 2026-10-21, see the
    executor header notes): find_library/get_library reject anything but
    the full display name (SystemError 'stDisplayName'), get_library
    rejects repository=None (TypeError) - lookups must enumerate
    get_all_libraries, so the fake makes the native calls fail loudly."""

    def __init__(self, repos):
        self.repositories = list(repos)

    def get_all_libraries(self, arg=True):
        # bool -> whole manager (True: exclude shadowed); repo -> that repo
        if isinstance(arg, bool):
            libs = []
            for pos, repo in enumerate(self.repositories):
                for lib in repo.libs.values():
                    shadowed = any(r.libs.get(lib.name)
                                   for r in self.repositories[:pos])
                    if not arg or not shadowed:
                        libs.append(lib)
            return libs
        return list(arg.libs.values())

    def find_library(self, display_name):
        raise SystemError("stDisplayName")

    def get_library(self, name, repository=None):
        if repository is None:
            raise TypeError("Value cannot be null.\r\nParameter name: repo")
        raise SystemError("stDisplayName")

    def get_file_path(self, lib):
        return r"C:\libs\{}.library".format(lib.displayname)

    def install_library(self, filepath, repository=None, overwrite=False):
        repo = repository if repository is not None \
            else self.repositories[0]
        displayname = os.path.basename(filepath).rsplit(".", 1)[0]
        if displayname in repo.libs and not overwrite:
            raise RuntimeError("library already exists")
        lib = FakeLib(displayname, company="Acme")
        repo.libs[displayname] = lib
        return lib

    def uninstall_library(self, repository, library):
        repository.libs.pop(library.name, None)

    def insert_repository(self, rootfolder, name, index=-1):
        repo = FakeRepo(name, rootfolder)
        if index < 0:
            self.repositories.append(repo)
        else:
            self.repositories.insert(index, repo)
        return repo

    def remove_repository(self, repository, delete_on_disk=False):
        self.repositories.remove(repository)

    def update_repository(self, repository, new_name=None,
                          new_location=None, copy_libraries=False):
        if new_name:
            repository.name = new_name
        if new_location:
            repository.root_folder = new_location

    def move_repository(self, repository, new_index):
        self.repositories.remove(repository)
        if new_index < 0:
            self.repositories.append(repository)
        else:
            self.repositories.insert(new_index, repository)


@pytest.fixture()
def mgr(monkeypatch):
    system = FakeRepo("System", r"C:\SystemLibs", editable=False)
    wago = FakeRepo("WAGO", r"C:\WagoLibs")
    wago.libs["WagoAppDateTime"] = FakeLib(
        "WagoAppDateTime", version="1.2.3.4", namespace="WagoAppDateTime")
    user = FakeRepo("User", r"C:\UserLibs")
    user.libs["MyHelpers"] = FakeLib("MyHelpers", displayname="My Helpers",
                                     version="0.1.0.0")
    fake = FakeLibManager([system, wago, user])
    monkeypatch.setattr(ex, "librarymanager", fake)
    return fake


def test_libs_lists_shadowed_and_scoped(mgr):
    shadow = FakeLib("WagoAppDateTime", version="9.9.9.9")
    mgr.repositories[0].libs["WagoAppDateTime"] = shadow  # system shadows

    out = ex.cmd_libs({})
    assert out["count"] == 2  # shadowed WAGO copy excluded by default
    versions = {lib["version"] for lib in out["libraries"]}
    assert "1.2.3.4" not in versions   # shadowed by the System copy
    assert "9.9.9.9" in versions       # the shadowing System copy stays
    assert out["shadowed_included"] is False

    out = ex.cmd_libs({"shadowed": True})
    assert out["count"] == 3
    assert out["shadowed_included"] is True

    out = ex.cmd_libs({"repo": "WAGO"})
    assert out["count"] == 1
    assert out["libraries"][0]["repo"] == "WAGO"
    assert out["libraries"][0]["path"].endswith(".library")

    with pytest.raises(RuntimeError, match="unknown library repository"):
        ex.cmd_libs({"repo": "Nope"})


def test_lib_find_displayname_bare_title_and_shadowed(mgr):
    out = ex.cmd_lib_find({"name": "My Helpers"})  # display name
    assert out["count"] == 1
    assert out["matches"][0]["repo"] == "User"

    out = ex.cmd_lib_find({"name": "MyHelpers"})   # library name key
    assert out["count"] == 1
    assert out["matches"][0]["repo"] == "User"

    # exact wants the full display name / name - the bare-title prefix
    # form is a non-exact convenience only
    assert ex.cmd_lib_find({"name": "My Helpers", "exact": True})[
        "count"] == 1
    assert ex.cmd_lib_find({"name": "My Helper", "exact": True})[
        "count"] == 0

    out = ex.cmd_lib_find({"name": "WagoAppDateTime", "exact": True})
    assert out["matches"][0]["version"] == "1.2.3.4"

    # a same-name copy in an earlier repository is reported shadowed
    mgr.repositories[0].libs["MyHelpers"] = FakeLib(
        "MyHelpers", displayname="My Helpers", version="9.9.9.9")
    out = ex.cmd_lib_find({"name": "My Helpers"})
    assert out["count"] == 2
    flags = {(m["repo"], m["shadowed"]) for m in out["matches"]}
    assert ("System", False) in flags   # first in search order: wins
    assert ("User", True) in flags      # shadowed by the System copy

    with pytest.raises(RuntimeError, match="needs args.name"):
        ex.cmd_lib_find({})


def test_lib_install_first_repo_named_repo_overwrite_gate(mgr, tmp_path):
    lib_file = tmp_path / "FreshLib.library"
    lib_file.write_bytes(b"x")

    out = ex.cmd_lib_install({"path": str(lib_file)})
    assert out["repo"] == "System"  # first repository
    assert mgr.repositories[0].libs["FreshLib"].displayname == "FreshLib"

    ex.cmd_lib_install({"path": str(lib_file), "repo": "User"})
    assert "FreshLib" in mgr.repositories[2].libs

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_lib_install({"path": str(lib_file), "overwrite": True})
    ex.cmd_lib_install({"path": str(lib_file), "repo": "User",
                        "overwrite": True, "confirm": True})

    with pytest.raises(RuntimeError, match="not found"):
        ex.cmd_lib_install({"path": str(tmp_path / "gone.library")})


def test_lib_uninstall_gate_ambiguity_and_verify(mgr):
    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_lib_uninstall({"name": "MyHelpers"})

    # install a second copy elsewhere -> ambiguous without repo
    mgr.repositories[0].libs["MyHelpers"] = FakeLib("MyHelpers",
                                    displayname="My Helpers")
    with pytest.raises(RuntimeError, match="ambiguous"):
        ex.cmd_lib_uninstall({"name": "MyHelpers", "confirm": True})

    # two variants INSIDE one repository -> name the exact display name
    mgr.repositories[2].libs["MyHelpersOld"] = FakeLib(
        "MyHelpersOld", displayname="My Helpers, 0.0.9.0")
    with pytest.raises(RuntimeError, match="full display name"):
        ex.cmd_lib_uninstall({"name": "My Helpers", "repo": "User",
                              "confirm": True})

    out = ex.cmd_lib_uninstall({"name": "My Helpers", "repo": "User",
                                "exact": True, "confirm": True})
    assert out == {"uninstalled": "My Helpers", "repo": "User",
                   "verified_gone": True}

    with pytest.raises(RuntimeError, match="not found"):
        ex.cmd_lib_uninstall({"name": "Ghost", "confirm": True})


def test_repos_reports_order_editable_counts(mgr):
    out = ex.cmd_repos({})
    names = [r["name"] for r in out["repositories"]]
    assert names == ["System", "WAGO", "User"]
    assert out["repositories"][0]["editable"] is False
    assert out["repositories"][1]["libraries"] == 1


def test_repo_crud_gates_and_effects(mgr, tmp_path):
    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_repo_add({"rootfolder": str(tmp_path), "name": "New"})
    with pytest.raises(RuntimeError, match="existing directory"):
        ex.cmd_repo_add({"rootfolder": str(tmp_path / "nope"),
                         "name": "New", "confirm": True})

    ex.cmd_repo_add({"rootfolder": str(tmp_path), "name": "New",
                     "confirm": True})
    assert [r.name for r in mgr.repositories] == \
        ["System", "WAGO", "User", "New"]

    with pytest.raises(RuntimeError, match="not editable"):
        ex.cmd_repo_remove({"name": "System", "confirm": True})
    ex.cmd_repo_remove({"name": "New", "confirm": True})

    ex.cmd_repo_update({"name": "User", "new_name": "Mine",
                        "confirm": True})
    assert mgr.repositories[2].name == "Mine"

    out = ex.cmd_repo_move({"name": "Mine", "index": 0})
    assert out["order"] == ["Mine", "System", "WAGO"]
    out = ex.cmd_repo_move({"name": "Mine", "index": -1})
    assert out["order"] == ["System", "WAGO", "Mine"]

    with pytest.raises(RuntimeError, match="needs args.index"):
        ex.cmd_repo_move({"name": "Mine"})


def test_libs_paging_and_filter(mgr):
    user = mgr.repositories[2]
    for i in range(250):
        user.libs["Bulk{0:03d}".format(i)] = FakeLib(
            "BulkLib{0:03d}".format(i), displayname="Bulk {0:03d} lib".format(i))
    user.libs["Needle"] = FakeLib("NeedleLib", displayname="The Needle, 1.0")

    # default limit pages the 253 matches (250 bulk + needle + the two
    # fixture libs in User/WAGO)
    out = ex.cmd_libs({})
    assert out["total"] == 253
    assert out["count"] == 200
    assert out["truncated"] is True
    assert out["offset"] == 0

    # walk to the last page: 53 entries, no truncation
    out = ex.cmd_libs({"offset": 200})
    assert out["count"] == 53
    assert out["truncated"] is False

    # past the end -> empty page
    out = ex.cmd_libs({"offset": 1000})
    assert out["count"] == 0 and out["total"] == 253
    assert out["truncated"] is False

    # limit=0 means unlimited
    out = ex.cmd_libs({"limit": 0})
    assert out["count"] == 253 and out["truncated"] is False

    # filter is a case-insensitive substring on displayname/title/name
    out = ex.cmd_libs({"filter": "needle"})
    assert out["total"] == 1
    assert out["libraries"][0]["displayname"] == "The Needle, 1.0"
    out = ex.cmd_libs({"filter": "bulklib249"})
    assert out["total"] == 1

    # scoped + paged together
    out = ex.cmd_libs({"repo": "User", "limit": 10})
    assert out["count"] == 10 and out["total"] == 252
    assert all(lib["repo"] == "User" for lib in out["libraries"])


def test_export_reporter_base_resolution(monkeypatch):
    # offline: ExportReporter stand-in is None -> the IExportReporter
    # placeholder class is the base
    base = ex._export_reporter_base()
    assert base is ex.IExportReporter

    # a real injected interface (any non-None) wins under its own name
    marker = type("RealExportReporter", (object,), {})
    monkeypatch.setattr(ex, "ExportReporter", marker)
    assert ex._export_reporter_base() is marker

    # neither name present -> plain-object fallback (never called, but
    # keeps the factory importable)
    monkeypatch.setattr(ex, "ExportReporter", None)
    monkeypatch.setattr(ex, "IExportReporter", None)
    assert ex._export_reporter_base() is object


def test_export_target_path_sanitized(tmp_path):
    # _short_name's "Name=<x>" prefix is stripped, bad chars underscored
    assert ex._export_target_path(str(tmp_path), "Name=My App").endswith(
        "My_App.xml")
    assert ex._export_target_path(str(tmp_path), "Name=A/B:C").endswith(
        "A_B_C.xml")
    assert ex._export_target_path(str(tmp_path), "").endswith(
        "application.xml")
    assert ex._export_target_path(str(tmp_path), "Name=").endswith(
        "application.xml")


class FakeArchiveProject(object):
    """Records save_archive calls; raises per configured form."""

    def __init__(self, failures):
        # failures: list of exception-or-None per form index
        self.failures = list(failures)
        self.calls = []

    def save_archive(self, *argv):
        self.calls.append(argv)
        idx = len(self.calls) - 1
        failure = self.failures[idx] if idx < len(self.failures) else None
        if failure is not None:
            raise failure
        # "write" the archive the executor then verifies
        with open(argv[0], "wb") as fh:
            fh.write(b"archive")


def test_save_archive_ladder(tmp_path):
    # form 1 works -> used, reported
    good = FakeArchiveProject([None])
    out = ex.cmd_save_archive(good, {"path": str(tmp_path / "a.parch")})
    assert out["form"] == 1 and out["bytes"] == 7
    assert good.calls == [(str(tmp_path / "a.parch"),)]

    # form 1 NREs live-style -> form 2 saves the day
    nre = RuntimeError("Object reference not set")
    fallback = FakeArchiveProject([nre, None])
    out = ex.cmd_save_archive(fallback, {"path": str(tmp_path / "b.parch")})
    assert out["form"] == 2
    assert fallback.calls[0] == (str(tmp_path / "b.parch"),)
    assert fallback.calls[1] == (str(tmp_path / "b.parch"), None)

    # every form fails -> error lists attempts and points at snapshot
    doomed = FakeArchiveProject([nre, nre, nre])
    with pytest.raises(RuntimeError, match="ecockpit_snapshot"):
        ex.cmd_save_archive(doomed, {"path": str(tmp_path / "c.parch")})
    assert len(doomed.calls) == 3

    # a form that returns without writing the file is not trusted
    liar = FakeArchiveProject([None])
    liar.save_archive = lambda *a: None
    with pytest.raises(RuntimeError, match="ecockpit_snapshot"):
        ex.cmd_save_archive(liar, {"path": str(tmp_path / "d.parch")})


def test_online_commands_reach_dispatch_without_unbound_project():
    # regression: the dispatch branches for login / source_download /
    # prepared_values must call get_project() before using it (live
    # 2026-10-21: UnboundLocalError because the branch referenced the
    # dispatcher-local `project` without assigning it)
    for cmd in ("login", "source_download", "prepared_values"):
        out = ex.run_command({"id": "t-" + cmd, "cmd": cmd,
                              "args": {"confirm": True}})
        assert "referenced before assignment" not in (out.get("error") or "")
        assert out["error"], "expected a real offline error (no project)"


class FakeUI(object):
    """Plain-Python system.ui fake: records every method call, returns
    canned results in order."""

    def __init__(self, results=None):
        self.results = list(results or [])
        self.calls = []

    def __getattr__(self, name):
        def _call(*argv):
            self.calls.append((name,) + argv)
            if self.results:
                return self.results.pop(0)
            return None
        return _call


class FakeSystem(object):
    """The injected `system` object per the ScriptSystem stub: value
    attributes set in __init__, method calls recorded."""

    def __init__(self, **kwargs):
        self.ui = FakeUI()
        self.prompt_answers = {}
        self.calls = []
        self.ui_present = True
        self.executable_filename = r"C:\e!COCKPIT\e!COCKPIT.exe"
        self.trace = False
        self.abortable = True
        self.aborting = False
        self.abort_autocheck = True
        self.background_loading_of_libraries_finished = False
        self.prompt_handling = 2
        self.commands = []
        self.factories = []
        for k, v in kwargs.items():
            setattr(self, k, v)

    def __getattr__(self, name):
        def _call(*argv):
            self.calls.append((name,) + argv)
            return None
        return _call


@pytest.fixture()
def sysobj(monkeypatch):
    fake = FakeSystem()
    monkeypatch.setattr(ex, "system", fake)
    return fake


def test_system_flags_decode_and_enum_helpers():
    assert ex._flags_to_names(3, ex._PROMPT_HANDLING_FLAGS) == \
        ["ForwardSimplePrompts", "LogSimplePrompts"]
    assert ex._flags_to_names(0, ex._PROMPT_HANDLING_FLAGS) == ["NONE"]
    assert ex._flags_to_names(32, ex._PROMPT_HANDLING_FLAGS) == ["0x20"]
    assert ex._enum_to_name(2, ex._PROMPT_RESULT_VALUES) == "Yes"
    assert ex._enum_to_name(99, ex._PROMPT_RESULT_VALUES) == "99"

    # stand-in enum globals (None) fall back to the stub int table
    assert ex._enum_by_names("PromptResult", ["Yes"],
                             ex._PROMPT_RESULT_VALUES, None) == 2

    # injected enum globals are preferred (getattr on them is established)
    class FakePH(object):
        LogSimplePrompts = 2
        LogMessageKeys = 16

    assert ex._enum_by_names("PromptHandling flag",
                             ["LogSimplePrompts", "LogMessageKeys"],
                             ex._PROMPT_HANDLING_FLAGS, FakePH) == 18

    # G-20: the LIVE enum can lack a member the stub documents
    # (SymbolCommentFilterType has no 'none') - stub int fallback
    class FakeNoNone(object):
        NormalComments = 1

    assert ex._enum_by_names("SymbolCommentFilterType", ["none"],
                             ex._SYMBOL_COMMENT_FILTER_TYPES,
                             FakeNoNone()) == 0
    assert ex._enum_by_names("SymbolCommentFilterType",
                             ["NormalComments"],
                             ex._SYMBOL_COMMENT_FILTER_TYPES,
                             FakeNoNone()) == 1
    with pytest.raises(RuntimeError, match="unknown PromptHandling"):
        ex._enum_by_names("PromptHandling flag", ["Nope"],
                          ex._PROMPT_HANDLING_FLAGS, None)


def test_system_info_and_write_message(sysobj):
    out = ex.cmd_system({"op": "info"})
    assert out["ui_present"] is True
    assert out["prompt_handling"] == ["LogSimplePrompts"]
    assert out["prompt_answer_keys"] == []
    assert out["commands"] == []

    out = ex.cmd_system({"op": "write_message", "text": "hello",
                         "severity": "Warning"})
    assert out["written"] is True
    # stand-in Severity (None) -> stub int 4 (Warning)
    assert sysobj.calls == [("write_message", 4, "hello")]

    with pytest.raises(RuntimeError, match="needs args.text"):
        ex.cmd_system({"op": "write_message"})


def test_system_set_prompt_handling_gate_and_combo(sysobj):
    # a dialog-free combination needs no confirm
    out = ex.cmd_system({"op": "set_prompt_handling",
                         "value": "LogSimplePrompts+LogMessageKeys"})
    assert sysobj.prompt_handling == 18
    assert out["effective"] == ["LogSimplePrompts", "LogMessageKeys"]

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_system({"op": "set_prompt_handling",
                       "value": "ForwardSimplePrompts"})
    ex.cmd_system({"op": "set_prompt_handling",
                   "value": "ForwardSimplePrompts", "confirm": True})
    assert sysobj.prompt_handling == 1

    ex.cmd_system({"op": "set_prompt_handling", "value": "NONE"})
    assert sysobj.prompt_handling == 0

    with pytest.raises(RuntimeError, match="NONE cannot be combined"):
        ex.cmd_system({"op": "set_prompt_handling",
                       "value": "NONE+LogSimplePrompts"})
    with pytest.raises(RuntimeError, match="unknown PromptHandling"):
        ex.cmd_system({"op": "set_prompt_handling", "value": "Wat"})


def test_system_prompt_answers_persist(sysobj):
    out = ex.cmd_system({"op": "set_prompt_answer",
                         "key": "OverWriteFiles", "result": "Yes"})
    assert sysobj.prompt_answers["OverWriteFiles"] == 2  # stub int fallback
    assert out["result"] == "Yes"

    out = ex.cmd_system({"op": "prompt_answers"})
    assert out["answers"] == {"OverWriteFiles": "2"}

    out = ex.cmd_system({"op": "clear_prompt_answers"})
    assert out["cleared"] == 1
    assert sysobj.prompt_answers == {}

    for missing in ({"op": "set_prompt_answer", "key": "k"},
                    {"op": "set_prompt_answer", "result": "Yes"}):
        with pytest.raises(RuntimeError, match="args.key"):
            ex.cmd_system(missing)


def test_system_set_flags_and_progress(sysobj):
    out = ex.cmd_system({"op": "set_flags", "abortable": False})
    assert out["set"] == {"abortable": False}
    assert sysobj.abortable is False
    assert out["flags"]["aborting"] is False

    with pytest.raises(RuntimeError, match="at least one"):
        ex.cmd_system({"op": "set_flags"})

    ex.cmd_system({"op": "progress", "action": "start", "description": "x",
                   "items": 3, "unit": "objects"})
    ex.cmd_system({"op": "progress", "action": "step", "item": "a",
                   "completed": 2})
    ex.cmd_system({"op": "progress", "action": "absolute", "item": "a",
                   "absolute_progress": 2})
    assert [c[0] for c in sysobj.calls] == \
        ["progress_start", "progress_step", "progress_absolute"]
    assert sysobj.calls[0] == ("progress_start", "x", 3, "objects")

    with pytest.raises(RuntimeError, match="start\\|step\\|absolute"):
        ex.cmd_system({"op": "progress", "action": "warp"})


def test_system_ui_gate_methods_and_secret_masking(sysobj):
    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_system({"op": "ui", "method": "prompt", "message": "m"})
    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_system({"op": "exit"})

    out = ex.cmd_system({"op": "ui", "method": "prompt", "message": "Overwrite?",
                         "choice": "YesNo", "default_result": "No",
                         "confirm": True})
    assert out["result"] is None  # FakeUI returns None -> reported as None
    assert sysobj.ui.calls[0][0] == "prompt"
    # stand-in enums -> stub ints: YesNo=2, No=3
    assert sysobj.ui.calls[0][1:4] == ("Overwrite?", 2, 3)

    # sensitive results are returned but never logged
    sysobj.ui.results = ["s3cret"]
    out = ex.cmd_system({"op": "ui", "method": "query_password",
                         "message": "pw", "confirm": True})
    assert out["result"] == "s3cret"
    assert all("s3cret" not in ln for ln in ex._lines)

    with pytest.raises(RuntimeError, match="unknown ui method"):
        ex.cmd_system({"op": "ui", "method": "explode", "confirm": True})
    with pytest.raises(RuntimeError, match="args.message"):
        ex.cmd_system({"op": "ui", "method": "error", "confirm": True})


def test_system_exit_gate_and_call(sysobj):
    out = ex.cmd_system({"op": "exit", "code": 7, "confirm": True})
    assert out["exiting"] is True and out["code"] == 7
    assert sysobj.calls == [("exit", 7)]


def test_system_command_reaches_dispatch(sysobj):
    # dispatch branch (system-level, no get_project) + headless hardening
    # run against the fake without breaking
    out = ex.run_command({"id": "t-sys", "cmd": "system",
                          "args": {"op": "info"}})
    assert out["ok"] is True
    assert out["value"]["ui_present"] is True


class FakeDeviceId(object):
    def __init__(self):
        self.type = 20
        self.id = "0x1130dd01"
        self.version = "3.10.4.0"


class FakeDriverInfo(object):
    """ScriptDriverInfo per the stub: value attrs + setter recordings."""

    def __init__(self):
        self.can_set_io_application = True
        self.io_application = "guid-app"
        self.update_ios_while_in_stop = True
        self.behaviour_for_outputs_on_stop = 0
        self.user_program_for_stop_reset_behaviour = ""
        self.always_update_variables = 2
        self.bus_cycle_task_by_name = "Monitor"
        self.bus_cycle_task_by_guid = "guid-task"
        self.generate_force_variables = False
        self.enable_diagnosis = True
        self.show_io_warnings_as_errors = False
        self.calls = []

    def set_bus_cycle_task(self, name):
        self.calls.append(("set_bus_cycle_task", name))
        self.bus_cycle_task_by_name = name

    def set_io_application(self, app):
        self.calls.append(("set_io_application", app))


class FakeDevice(object):
    def __init__(self):
        self.device_id = FakeDeviceId()
        self.driver_info = FakeDriverInfo()
        self.simulation = False
        self.module_id = "mod-1"

    def get_device_identification(self):
        return self.device_id

    def get_module_identification(self):
        return self.module_id

    def get_simulation_mode(self):
        return self.simulation

    def set_simulation_mode(self, value):
        self.simulation = bool(value)


class FakeAppMarker(object):
    is_application = True


class FakeFindProject(object):
    def find(self, name, recursive):
        return [FakeAppMarker()]


@pytest.fixture()
def devobj(monkeypatch):
    fake = FakeDevice()
    monkeypatch.setattr(ex, "get_device", lambda project, name="": fake)
    return fake


def test_device_info_identification_and_simulation(devobj):
    out = ex.cmd_device_info(None, {"op": "identification"})
    assert out["device_id"] == {"type": "20", "id": "0x1130dd01",
                                "version": "3.10.4.0"}
    assert out["module_id"] == "mod-1"

    out = ex.cmd_device_info(None, {"op": "simulation"})
    assert out["simulation"] is False

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_device_info(None, {"op": "simulation_set", "enabled": True})
    out = ex.cmd_device_info(None, {"op": "simulation_set",
                                    "enabled": True, "confirm": True})
    assert devobj.simulation is True
    assert out["simulation"] is True


def test_device_info_driver_info_read_and_set(devobj):
    out = ex.cmd_device_info(None, {"op": "driver_info"})
    di = out["driver_info"]
    assert di["bus_cycle_task_by_name"] == "Monitor"
    assert di["behaviour_for_outputs_on_stop"] == "KeepCurrentValues"
    assert di["always_update_variables"] == "OnlyIfUnused"

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_device_info(None, {"op": "driver_info_set",
                                  "bus_cycle_task": "PlcTask"})
    with pytest.raises(RuntimeError, match="at least one"):
        ex.cmd_device_info(None, {"op": "driver_info_set",
                                  "confirm": True})

    out = ex.cmd_device_info(FakeFindProject(), {
        "op": "driver_info_set", "confirm": True,
        "bus_cycle_task": "PlcTask", "io_application": "Application",
        "behaviour_for_outputs_on_stop": "SetToDefault",
        "always_update_variables": "AlwaysInBusCycle",
        "enable_diagnosis": False})
    assert devobj.driver_info.bus_cycle_task_by_name == "PlcTask"
    assert devobj.driver_info.behaviour_for_outputs_on_stop == 1
    assert devobj.driver_info.always_update_variables == 3
    assert devobj.driver_info.enable_diagnosis is False
    assert out["applied"]["io_application"] == "Application"
    io_calls = [c for c in devobj.driver_info.calls
                if c[0] == "set_io_application"]
    assert len(io_calls) == 1 and isinstance(io_calls[0][1], FakeAppMarker)
    assert out["driver_info"]["bus_cycle_task_by_name"] == "PlcTask"


class FakeIoMapping(object):
    """ScriptIoMapping per the stub: setting manual_iec_address to an
    address flips automatic off; None flips it back on."""

    def __init__(self, variable="", default_variable="",
                 auto=True, manual=None):
        self.Id = 1
        self.variable = variable
        self.default_variable = default_variable
        self.mapping_creates_variable = True
        self.maps_to_existing_variable = False
        self._auto = auto
        self._manual = manual

    @property
    def automatic_iec_address(self):
        return self._auto

    @automatic_iec_address.setter
    def automatic_iec_address(self, value):
        self._auto = bool(value)

    @property
    def manual_iec_address(self):
        return self._manual

    @manual_iec_address.setter
    def manual_iec_address(self, value):
        if value is None:
            self._auto = True
            self._manual = None
        else:
            self._auto = False
            self._manual = value


class FakeIoElement(object):
    def __init__(self, identifier, mapping=None, mappable=False):
        self.identifier = identifier
        self.visible_name = identifier
        self._mapping = mapping
        self._mappable = mappable or mapping is not None
        self.has_sub_elements = False

    @property
    def is_mappable_io(self):
        return self._mappable

    @property
    def io_mapping(self):
        if self._mapping is None:
            raise RuntimeError("no mapping (device parameter)")
        return self._mapping

    def __iter__(self):
        return iter(())


class FakeParamSet(object):
    """Parameter set: iterable of FakeParam. String indexing FAILS like
    the live .NET set (positional indexer, 2026-10-22) - lookups must
    enumerate."""

    def __init__(self, params):
        self._params = [FakeParam(name, els) for name, els in params]

    def __iter__(self):
        return iter(self._params)

    def __getitem__(self, name):
        raise IndexError("Index was out of range. Must be non-negative "
                         "and less than the size of the collection. "
                         "Parameter name: index")


class FakeParam(object):
    def __init__(self, name, elements):
        self.name = name
        self._elements = elements

    def __iter__(self):
        return iter(self._elements)


class FakeConnector(object):
    def __init__(self, cid, params):
        self.id = cid
        self.host_parameters = params


class FakeIoDevice(object):
    is_device = True

    def __init__(self, device_set, connectors=()):
        self._device_set = device_set
        self._connectors = list(connectors)
        self.exported = None
        self.imported = None

    def __str__(self):
        return "ScriptDeviceObject{Name=FakeDev}"

    def get_name(self):
        return "FakeDev"

    @property
    def device_parameters(self):
        return self._device_set

    @property
    def connectors(self):
        return self._connectors

    def export_io_mappings_as_csv(self, path):
        self.exported = path
        with open(path, "wb") as fh:
            fh.write(b"a,b\n1,2\n")

    def import_io_mappings_from_csv(self, path):
        self.imported = path


class FakeDevicesProject(object):
    def __init__(self, devices):
        self._devices = devices

    def get_children(self, recursive):
        return list(self._devices)


@pytest.fixture()
def iodev(monkeypatch):
    input_map = FakeIoMapping(variable="old_var", auto=True)
    conn_map = FakeIoMapping(variable="c_var", auto=False, manual="%QW0")
    device = FakeIoDevice(
        FakeParamSet([("IO-Params", [FakeIoElement("plain_param", None),
                                     FakeIoElement("unmapped_io",
                                                   None, mappable=True)]),
                      ("IO-Map", [FakeIoElement("input_io", input_map)])]),
        [FakeConnector(1, FakeParamSet([
            ("ConnIo", [FakeIoElement("conn_io", conn_map)])]))])
    monkeypatch.setattr(ex, "get_device", lambda project, name="": device)
    device.input_map = input_map
    device.conn_map = conn_map
    return device


def test_device_io_mappings_walk(iodev):
    # a plugged submodule below the root carries its own mapped channel
    # (LIVE 2026-10-22: the root-only walk missed the module channels
    # the engine's own CSV export lists)
    sub_map = FakeIoMapping(variable="s_var", auto=False, manual="%IX2.0")
    sub = FakeIoDevice(
        FakeParamSet([("ModIo", [FakeIoElement("sub_io", sub_map)])]))
    sub.get_name = lambda: "SubDev"

    out = ex.cmd_device_io(FakeDevicesProject([iodev, sub]),
                           {"op": "mappings"})
    assert out["count"] == 4
    by_id = dict((m["identifier"], m) for m in out["mappings"])
    assert "plain_param" not in by_id          # not mappable -> skipped
    assert by_id["input_io"]["io_mapping"]["variable"] == "old_var"
    assert by_id["input_io"]["io_mapping"]["automatic_iec_address"] is True
    assert by_id["input_io"]["device"] == "FakeDev"
    assert by_id["conn_io"]["parameter"] == "ConnIo"
    assert by_id["conn_io"]["source"] == "connector 1"
    assert by_id["conn_io"]["io_mapping"]["manual_iec_address"] == "%QW0"
    # mappable but unmapped -> listed with a null mapping
    assert by_id["unmapped_io"]["io_mapping"] is None
    # submodule channels are found via the project-tree walk
    assert by_id["sub_io"]["device"] == "SubDev"
    assert by_id["sub_io"]["io_mapping"]["variable"] == "s_var"


def test_device_io_set_mapping_gates_and_effects(iodev):
    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_device_io(None, {"op": "set_mapping", "name": "x",
                                "variable": "GVL.new"})

    out = ex.cmd_device_io(None, {"op": "set_mapping", "name": "IO-Map",
                                  "variable": "GVL.new", "confirm": True})
    assert out["elements"][0]["set"] == {"variable": "GVL.new"}
    assert out["elements"][0]["readback"]["variable"] == "GVL.new"

    # automatic=true resets the address (manual -> None -> auto)
    out = ex.cmd_device_io(None, {"op": "set_mapping", "name": "ConnIo",
                                  "automatic": True, "confirm": True})
    assert iodev.conn_map.manual_iec_address is None
    assert iodev.conn_map.automatic_iec_address is True
    assert out["elements"][0]["set"] == {"manual_iec_address": None}

    # manual address flips automatic off
    ex.cmd_device_io(None, {"op": "set_mapping", "name": "ConnIo",
                            "manual_iec_address": "%QW7", "confirm": True})
    assert iodev.conn_map.automatic_iec_address is False
    assert iodev.conn_map.manual_iec_address == "%QW7"

    with pytest.raises(RuntimeError, match="no mappable element"):
        ex.cmd_device_io(None, {"op": "set_mapping", "name": "IO-Params",
                                "variable": "GVL.x", "confirm": True})
    with pytest.raises(RuntimeError, match="needs args.variable"):
        ex.cmd_device_io(None, {"op": "set_mapping", "name": "ConnIo",
                                "confirm": True})


def test_device_io_set_mapping_multi_element_guard(iodev, monkeypatch):
    # G-21: a multi-channel parameter refuses an unfiltered set
    two = FakeIoDevice(FakeParamSet([
        ("Multi", [
            FakeIoElement("ch1", FakeIoMapping(variable="a",
                                               auto=False, manual="%IX0")),
            FakeIoElement("ch2", FakeIoMapping(variable="b",
                                               auto=False, manual="%IX1")),
        ])]))
    monkeypatch.setattr(ex, "get_device", lambda project, name="": two)
    with pytest.raises(RuntimeError, match="REFUSED.*mappable elements"):
        ex.cmd_device_io(None, {"op": "set_mapping", "name": "Multi",
                                "variable": "c", "confirm": True})

    # element= targets exactly one channel
    out = ex.cmd_device_io(None, {"op": "set_mapping", "name": "Multi",
                                  "element": "ch2", "variable": "c",
                                  "confirm": True})
    assert len(out["elements"]) == 1
    assert out["elements"][0]["identifier"] == "ch2"
    assert out["elements"][0]["readback"]["variable"] == "c"

    # all_elements=true is the explicit escape hatch
    out = ex.cmd_device_io(None, {"op": "set_mapping", "name": "Multi",
                                  "variable": "d", "all_elements": True,
                                  "confirm": True})
    assert len(out["elements"]) == 2
    assert all(r["readback"]["variable"] == "d"
               for r in out["elements"])


def test_device_io_csv_round_trip(iodev, tmp_path, monkeypatch):
    out = ex.cmd_device_io(None, {"op": "export_csv",
                                  "file": str(tmp_path / "m.csv")})
    assert out["bytes"] == 8
    assert iodev.exported == os.path.abspath(str(tmp_path / "m.csv"))

    # default location derives from PROJECT_ROOT + sanitized device name
    monkeypatch.setattr(ex, "PROJECT_ROOT", str(tmp_path))
    out = ex.cmd_device_io(None, {"op": "export_csv"})
    expected = os.path.join(str(tmp_path), "io-mappings",
                            "FakeDev_io_mappings.csv")
    assert os.path.isfile(expected)
    assert out["file"] == os.path.abspath(expected)

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_device_io(None, {"op": "import_csv",
                                "file": str(tmp_path / "m.csv")})
    out = ex.cmd_device_io(None, {"op": "import_csv",
                                  "file": str(tmp_path / "m.csv"),
                                  "confirm": True})
    assert out["imported"] is True
    assert iodev.imported == os.path.abspath(str(tmp_path / "m.csv"))

    with pytest.raises(RuntimeError, match="existing CSV"):
        ex.cmd_device_io(None, {"op": "import_csv",
                                "file": str(tmp_path / "gone.csv"),
                                "confirm": True})


class FakeSym(object):
    """ScriptSymbolConfigObject option surface per the stub."""

    is_symbol_config = True

    def __init__(self):
        self.content_feature_flags = 3          # SupportOPCUA|IncludeComments
        self.effective_content_feature_flags = 3
        self.symbol_attribute_filter_type = 0
        self.effective_symbol_attribute_filter_type = 0
        self.symbol_attribute_filter_data = ""
        self.symbol_comment_filter_type = 0
        self.effective_symbol_comment_filter_type = 0
        self.enable_direct_io_access = False
        self.xsd_bytes = [60, 63, 120, 109, 108]  # "<?xml"

    def check_effective_direct_io_access(self):
        return 0

    def get_symbol_configuration_xsd(self):
        return list(self.xsd_bytes)


class FakeSymApp(object):
    def __init__(self):
        self.sym = FakeSym()

    def get_children(self, recursive):
        return [self.sym]


@pytest.fixture()
def symapp(monkeypatch):
    app = FakeSymApp()
    monkeypatch.setattr(ex, "get_application", lambda project: app)
    return app


def test_symbol_config_options_read_and_set(symapp):
    out = ex.cmd_symbol_config(None, {"op": "config"})
    cfg = out["config"]
    assert cfg["content_feature_flags"] == ["SupportOPCUA",
                                            "IncludeComments"]
    assert cfg["symbol_attribute_filter_type"] == "none"
    assert cfg["direct_io_obstacles"] == "none"

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_symbol_config(None, {"op": "config_set",
                                    "content_feature_flags": "SupportOPCUA"})

    out = ex.cmd_symbol_config(None, {"op": "config_set", "confirm": True,
                                      "content_feature_flags":
                                      "SupportOPCUA+IncludeAttributes",
                                      "attribute_filter_type": "Prefix",
                                      "attribute_filter_data": "GVL_",
                                      "comment_filter_type": "Both"})
    sym = symapp.sym
    assert sym.content_feature_flags == 1 | 4
    assert sym.symbol_attribute_filter_type == 3
    assert sym.symbol_attribute_filter_data == "GVL_"
    assert sym.symbol_comment_filter_type == 3
    assert out["applied"]["attribute_filter_type"] == "Prefix"
    assert out["config"]["symbol_attribute_filter_type"] == "Prefix"

    # standalone filter data update + none reset
    ex.cmd_symbol_config(None, {"op": "config_set", "confirm": True,
                                "attribute_filter_data": "PB_"})
    assert sym.symbol_attribute_filter_data == "PB_"
    ex.cmd_symbol_config(None, {"op": "config_set", "confirm": True,
                                "content_feature_flags": "none"})
    assert sym.content_feature_flags == 0

    with pytest.raises(RuntimeError, match="at least one"):
        ex.cmd_symbol_config(None, {"op": "config_set", "confirm": True})
    with pytest.raises(RuntimeError, match="unknown "
                                            "SymbolConfigContentFeature"):
        ex.cmd_symbol_config(None, {"op": "config_set", "confirm": True,
                                    "content_feature_flags": "Nope"})


def test_symbol_config_create_ladder(monkeypatch):
    calls = []
    created = FakeSym()

    class AppNoSym(object):
        def get_children(self, recursive):
            return []

        def create_symbol_config(self, export_comments, support_opc_ua,
                                 calc=None):
            calls.append(calc)
            if calc is False or calc is None:
                # live: the calculator parameter is a Guid
                raise TypeError("expected Guid, got bool")
            return created

    monkeypatch.setattr(ex, "get_application", lambda project: AppNoSym())
    out = ex.cmd_symbol_config(None, {"op": "create", "confirm": True})
    assert out["created"] is True
    assert calls == [False, None,
                     "00000000-0000-0000-0000-000000000000"]


def test_symbol_config_xsd_write(symapp, tmp_path):
    out = ex.cmd_symbol_config(None, {"op": "xsd",
                                      "file": str(tmp_path / "s.xsd")})
    assert out["bytes"] == 5
    assert out["preview"].startswith("<?xml")
    with open(str(tmp_path / "s.xsd"), "rb") as fh:
        assert fh.read() == b"<?xml"

    # default location under PROJECT_ROOT/plcopensrc-export
    old_root = ex.PROJECT_ROOT
    ex.PROJECT_ROOT = str(tmp_path)
    try:
        out = ex.cmd_symbol_config(None, {"op": "xsd"})
    finally:
        ex.PROJECT_ROOT = old_root
    expected = os.path.join(str(tmp_path), "plcopensrc-export",
                            "Symbolconfiguration.xsd")
    assert os.path.isfile(expected)
    assert out["file"] == os.path.abspath(expected)


class FakeSpecialPou(object):
    def __str__(self):
        return "ScriptObject{Name=CheckRangeSigned}"


class FakeSpecialContainer(object):
    def __init__(self):
        self.calls = []

    def create_pou(self, *argv, **kwargs):
        self.calls.append((argv, kwargs))
        return FakeSpecialPou()


def test_display_name_direct_access_not_getattr():
    # G-01: getattr() silently fails on the interop wrappers - the old
    # probe never ran live, so class-name reprs leaked into snapshot
    # filenames. Direct access works on wrappers and fakes alike.
    class P(object):
        def get_name(self):
            return "s4 v7"

        def __str__(self):
            return "Wago.Eng.Module.Script.ScriptObjects.EScriptProject"

    assert ex._display_name(P()) == "s4 v7"
    assert ex._snapshot_prefix(P()) == "s4_v7"

    class OnlyRepr(object):
        def __str__(self):
            return "ScriptThing{Name=FromRepr}"

    assert ex._snapshot_prefix(OnlyRepr()) == "Name_FromRepr"


def test_object_create_container_falls_back_to_application(monkeypatch):
    # G-22: EScriptProject has no create_pou live - the no-parent
    # default container is the APPLICATION
    class FakeSPT2(object):
        check_bounds = 2

    monkeypatch.setattr(ex, "SpecialPouType", FakeSPT2)
    app = FakeSpecialContainer()

    class RootProject(object):
        def find(self, name, recursive):
            return [app]

    monkeypatch.setattr(ex, "get_application", lambda project: app)
    out = ex.cmd_object_create(RootProject(), {
        "kind": "special_pou", "special_type": "check_bounds",
        "confirm": True})
    assert app.calls and out["created"] == "check_bounds"


def test_object_create_special_pou(monkeypatch):
    class FakeSPT(object):
        check_bounds = 2
        check_range_signed = 5

    monkeypatch.setattr(ex, "SpecialPouType", FakeSPT)
    container = FakeSpecialContainer()

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_object_create(container, {"kind": "special_pou",
                                         "special_type": "check_bounds"})

    out = ex.cmd_object_create(container, {"kind": "special_pou",
                                           "special_type": "check_bounds",
                                           "confirm": True})
    assert container.calls == [((2,), {})]   # positional SpecialPouType
    assert out["created"] == "check_bounds"
    assert "full recompile" in out["warning"]

    with pytest.raises(RuntimeError, match="special_type"):
        ex.cmd_object_create(container, {"kind": "special_pou",
                                         "confirm": True})
    with pytest.raises(RuntimeError, match="unknown SpecialPouType"):
        ex.cmd_object_create(container, {"kind": "special_pou",
                                         "special_type": "wat",
                                         "confirm": True})

    # no stand-in (None) -> same unknown-member error, not a crash
    monkeypatch.setattr(ex, "SpecialPouType", None)
    with pytest.raises(RuntimeError, match="unknown SpecialPouType"):
        ex.cmd_object_create(container, {"kind": "special_pou",
                                         "special_type": "check_bounds",
                                         "confirm": True})
