# -*- coding: utf-8 -*-
"""Offline unit tests for the library-curation executor commands.

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
