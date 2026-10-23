# -*- coding: utf-8 -*-
"""Offline tests for the tree / task-config / device-params executor
commands plus their MCP-tool wrappers (confirm gates, argument
pass-through).

The executor talks to ScriptEngine-injected globals (projects, enums).
Following the FakeLibManager pattern from test_executor_commands.py,
plain-Python fakes following the ScriptLib stubs are injected into the
module globals, so the command logic - including the DESTRUCTIVE paths
(rename/remove/move/folder, task create, ONLINE parameter write) - is
exercisable without e!COCKPIT.
"""

import json
import os

import pytest

os.environ["ECP_NO_RUN"] = "1"  # executor's import-time auto-build

from ecockpit_mcp import executor as ex  # noqa: E402
from ecockpit_mcp import server as srv  # noqa: E402
from ecockpit_mcp.server import ToolError  # noqa: E402


# --------------------------------------------------------------------------
# Project tree (ScriptTreeObject / ScriptObject stubs)
# --------------------------------------------------------------------------
class FakeTreeObj:
    """ScriptObject stand-in: marker attrs, rename/remove/move effects."""

    def __init__(self, name, guid="guid-x", is_folder=False, is_device=False):
        self._name = name
        self.guid = guid
        self.is_folder = is_folder
        self.is_device = is_device
        self.is_application = False
        self.is_task_configuration = False
        self.is_symbol_config = False
        self.is_libman = False
        self.is_textlist = False
        self.is_imagepool = False
        self.is_explicit_connector = False
        self.is_project_info = False
        self.has_textual_declaration = False
        self.has_textual_implementation = False
        self.renamed_to = None
        self.removed = False
        self.moved_to = None
        self.folders_created = []
        self.exclude_from_build = False  # via build_properties

    class _BP:
        exclude_from_build = False

    build_properties = _BP()

    def get_name(self):
        return self._name

    def rename(self, new_name):
        self._name = new_name
        self.renamed_to = new_name

    def remove(self):
        self.removed = True

    def move(self, parent, index):
        self.moved_to = (parent, index)

    def create_folder(self, name):
        self.folders_created.append(name)

    def __str__(self):
        return "ScriptObject{{Name={}, guid={}}}".format(self._name, self.guid)


class FakeTreeProject:
    def __init__(self, objects):
        self._objects = list(objects)
        self.folders_created = []

    def _live(self):
        return [o for o in self._objects if not o.removed]

    def find(self, name, recursive=True):
        return [o for o in self._live() if name.lower() in o.get_name().lower()]

    def get_children(self, recursive=True):
        return self._live()

    def create_folder(self, name):
        self.folders_created.append(name)


@pytest.fixture()
def treeproj():
    return FakeTreeProject(
        [
            FakeTreeObj("Device", is_device=True, guid="guid-1"),
            FakeTreeObj("Application", guid="guid-2"),
            FakeTreeObj("Pou_Main", guid="guid-3"),
            FakeTreeObj("Folder_Scripts", is_folder=True, guid="guid-4"),
        ]
    )


def test_tree_list_markers_filter_and_limit(treeproj):
    out = ex.cmd_tree(treeproj, {"op": "list"})
    assert out["total"] == 4 and out["count"] == 4
    by_name = {o["name"]: o for o in out["objects"]}
    assert by_name["Device"]["is_device"] is True
    assert by_name["Folder_Scripts"]["is_folder"] is True
    assert by_name["Pou_Main"]["guid"] == "guid-3"

    out = ex.cmd_tree(treeproj, {"op": "list", "name": "pou"})
    assert out["total"] == 1 and out["objects"][0]["name"] == "Pou_Main"

    out = ex.cmd_tree(treeproj, {"op": "list", "limit": 2})
    assert out["count"] == 2 and out["total"] == 4


def test_tree_rename_remove_move_folder_gates_and_effects(treeproj):
    for op, args in (
        ("rename", {"name": "Pou_Main", "new_name": "Pou_Renamed"}),
        ("remove", {"name": "Pou_Main"}),
        ("move", {"name": "Pou_Main", "new_parent": "Folder_Scripts"}),
        ("folder", {"new_name": "NewFolder"}),
    ):
        with pytest.raises(RuntimeError, match="REFUSED"):
            ex.cmd_tree(treeproj, dict(args, op=op, confirm=False))

    out = ex.cmd_tree(
        treeproj,
        {
            "op": "rename",
            "name": "Pou_Main",
            "new_name": "Pou_Renamed",
            "confirm": True,
        },
    )
    assert out["renamed"] is True and out["new_name"] == "Pou_Renamed"
    assert treeproj.find("Pou_Renamed")[0].guid == "guid-3"

    out = ex.cmd_tree(
        treeproj, {"op": "remove", "name": "Pou_Renamed", "confirm": True}
    )
    assert out["removed"] is True and out["was"]["name"] == "Pou_Renamed"
    assert treeproj.find("Pou_Renamed") == []

    out = ex.cmd_tree(
        treeproj,
        {
            "op": "move",
            "name": "Device",
            "new_parent": "Folder_Scripts",
            "index": 0,
            "confirm": True,
        },
    )
    device = treeproj.find("Device")[0]
    assert out["moved"] is True
    assert device.moved_to[1] == 0
    assert device.moved_to[0].get_name() == "Folder_Scripts"

    out = ex.cmd_tree(
        treeproj,
        {
            "op": "folder",
            "new_name": "Sub",
            "parent": "Folder_Scripts",
            "confirm": True,
        },
    )
    assert out["folder"] == "Sub"
    assert treeproj.find("Folder_Scripts")[0].folders_created == ["Sub"]
    out = ex.cmd_tree(treeproj, {"op": "folder", "new_name": "Root", "confirm": True})
    assert treeproj.folders_created == ["Root"]

    with pytest.raises(RuntimeError, match="no object named"):
        ex.cmd_tree(
            treeproj,
            {"op": "rename", "name": "Ghost", "new_name": "X", "confirm": True},
        )


# --------------------------------------------------------------------------
# Task configuration (ScriptTaskConfigObject stub)
# --------------------------------------------------------------------------
class FakeWatchdog:
    def __init__(self):
        self.enabled = False
        self.time = "100"
        self.time_unit = "ms"
        self.sensitivity = "1"


class FakeTaskPous:
    def __init__(self):
        self.items = []

    def add(self, name):
        self.items.append(name)

    def __iter__(self):
        return iter([(name,) for name in self.items])


class FakeTask:
    is_task = True

    def __init__(self, name):
        self._name = name
        self.kind_of_task = None
        self.interval = ""
        self.interval_unit = ""
        self.priority = ""
        self.event = None
        self.external_event = None
        self.core_binding = None
        self.parent_synchron_task = None
        self.watchdog = FakeWatchdog()
        self.pous = FakeTaskPous()

    def __str__(self):
        return "ScriptTaskObject{{Name={}}}".format(self._name)


class FakeTaskConfig:
    is_task_configuration = True

    def __init__(self):
        self.tasks = []
        self.created = []

    def get_children(self, recursive=True):
        return list(self.tasks)

    def create_task(self, name):
        task = FakeTask(name)
        self.tasks.append(task)
        self.created.append(name)
        return task

    def __str__(self):
        return "ScriptTaskConfigObject{Name=TaskConfiguration}"


class FakeTaskApp:
    def __init__(self, task_config=None):
        self._tc = task_config
        self.tc_created = False

    def get_children(self, recursive=True):
        return [self._tc] if self._tc is not None else []

    def create_task_configuration(self):
        self._tc = FakeTaskConfig()
        self.tc_created = True
        return self._tc

    def __str__(self):
        return "ScriptObject{Name=Application}"


class FakeNamedFindProject:
    def __init__(self, app):
        self._app = app

    def find(self, name, recursive=True):
        return [self._app] if name.lower() in str(self._app).lower() else []


@pytest.fixture()
def taskenv(monkeypatch):
    tc = FakeTaskConfig()
    main = FakeTask("MainTask")
    main.priority = "1"
    main.interval = "200"
    main.interval_unit = "ms"
    main.pous.add("PLC_PRG")
    tc.tasks.append(main)
    app = FakeTaskApp(tc)
    monkeypatch.setattr(ex, "get_application", lambda project: app)
    fake_kind = type(
        "KindOfTask",
        (),
        {
            "Cyclic": 1,
            "Freewheeling": 2,
            "Event": 3,
            "ExternalEvent": 4,
            "Status": 5,
            "ParentSynchron": 6,
        },
    )()
    monkeypatch.setattr(ex, "KindOfTask", fake_kind)
    return app, tc


def test_task_config_inspect_reports_tasks_and_watchdog(taskenv):
    app, tc = taskenv
    out = ex.cmd_task_config(None, {"op": "inspect"})
    assert out["application"] == "Name=Application"
    assert out["has_task_configuration"] is True
    assert len(out["tasks"]) == 1
    task = out["tasks"][0]
    assert task["name"] == "Name=MainTask"
    assert task["priority"] == "1"
    assert task["interval"] == "200"
    assert task["pous"] == ["PLC_PRG"]
    assert task["watchdog"] == {
        "enabled": False,
        "time": "100",
        "time_unit": "ms",
        "sensitivity": "1",
    }


def test_task_config_inspect_without_config_object(monkeypatch):
    app = FakeTaskApp(None)
    monkeypatch.setattr(ex, "get_application", lambda project: app)
    out = ex.cmd_task_config(None, {"op": "inspect"})
    assert out["has_task_configuration"] is False
    assert out["tasks"] == []


def test_task_create_gate_and_effects_without_existing_config(monkeypatch):
    app = FakeTaskApp(None)
    monkeypatch.setattr(ex, "get_application", lambda project: app)
    fake_kind = type("KindOfTask", (), {"Cyclic": 1})()
    monkeypatch.setattr(ex, "KindOfTask", fake_kind)

    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_task_config(None, {"op": "create_task", "name": "Monitor"})

    out = ex.cmd_task_config(
        None,
        {
            "op": "create_task",
            "name": "Monitor",
            "kind": "cyclic",
            "interval": "50",
            "priority": "4",
            "pous": ["PLC_PRG"],
            "watchdog_enabled": True,
            "watchdog_time": "2000",
            "confirm": True,
        },
    )
    assert out["created"] == "Monitor"
    assert app.tc_created is True  # configuration object created on demand
    task = app._tc.tasks[0]
    assert task.kind_of_task == 1  # KindOfTask.Cyclic via the injected enum
    assert task.interval == "50" and task.priority == "4"
    assert task.watchdog.enabled is True and task.watchdog.time == "2000"
    assert task.pous.items == ["PLC_PRG"]


def test_task_create_into_existing_config_and_named_app(taskenv, monkeypatch):
    app, tc = taskenv
    # named application resolves via project.find, not get_application
    monkeypatch.setattr(
        ex,
        "get_application",
        lambda project: (_ for _ in ()).throw(
            AssertionError("must not use the default app")
        ),
    )
    project = FakeNamedFindProject(app)
    out = ex.cmd_task_config(
        project,
        {
            "op": "create_task",
            "application": "Application",
            "name": "Second",
            "kind": "freewheeling",
            "confirm": True,
        },
    )
    assert out["created"] == "Second"
    assert [t._name for t in tc.tasks] == ["MainTask", "Second"]
    assert tc.tasks[1].kind_of_task == 2  # Freewheeling


# --------------------------------------------------------------------------
# Device parameters (ScriptDeviceParameters stub)
# --------------------------------------------------------------------------
class FakeEnumValue:
    def __init__(self, identifier):
        self.identifier = identifier


class FakeParamEl:
    """ScriptDataElement stand-in: one parameter element with offline
    value, online read/write and optional enum/range metadata."""

    def __init__(self, identifier, value="0", enum_values=None, rng=None):
        self.identifier = identifier
        self.visible_name = identifier
        self.unit = ""
        self.description = ""
        self.iec_type = "INT"
        self.can_access_online = True
        self.value = value
        self.is_enumeration = enum_values is not None
        self.allowed_values = [FakeEnumValue(v) for v in (enum_values or [])]
        self.is_range_type = rng is not None
        if rng:
            self.min_value, self.max_value = rng
        self.has_sub_elements = False
        self.writes = []

    def read_online_value(self, timeout=1000):
        return self.value

    def write_online_value(self, value):
        self.writes.append(value)
        self.value = str(getattr(value, "identifier", value))


class FakeParamsParam:
    def __init__(self, pid, name, elements, type_string="int"):
        self.id = pid
        self.name = name
        self.type_string = type_string
        self.section = "10: General"
        self.iec_type = "INT"
        self.param_type = ""
        self.channel_type = ""
        self._elements = list(elements)

    def __iter__(self):
        return iter(self._elements)


class FakeParamsSet:
    """Parameter set: iterable; string indexing FAILS like the live
    .NET set; by_id resolves numerically."""

    def __init__(self, params):
        self._params = list(params)

    def __iter__(self):
        return iter(self._params)

    def __getitem__(self, name):
        raise IndexError("Index was out of range. Parameter name: index")

    def by_id(self, pid):
        for p in self._params:
            if p.id == pid:
                return p
        raise IndexError("no parameter with id {}".format(pid))


class FakeConnectors:
    def __init__(self, by_id):
        self._by_id = by_id

    def by_id(self, cid):
        return self._by_id[cid]


class FakeParamsDevice:
    def __init__(self, device_set, connectors_by_id=None):
        self._device_set = device_set
        self._connectors = FakeConnectors(connectors_by_id or {})

    def get_name(self):
        return "PFC200_2ETH_RS"

    def __str__(self):
        return "ScriptDeviceObject{Name=PFC200_2ETH_RS}"

    @property
    def device_parameters(self):
        return self._device_set

    @property
    def connectors(self):
        return self._connectors


@pytest.fixture()
def paramsdev(monkeypatch):
    p1 = FakeParamsParam(1, "Board.Temp", [FakeParamEl("value", "31")])
    p2 = FakeParamsParam(
        2,
        "Board.Baudrate",
        [FakeParamEl("baud", "9600", enum_values=["9600", "19200"])],
    )
    p3 = FakeParamsParam(3, "Net.IP", [FakeParamEl("ip", "0.0.0.0")])
    conn_p = FakeParamsParam(7, "Conn.Param", [FakeParamEl("cp", "c")])
    device = FakeParamsDevice(
        FakeParamsSet([p1, p2, p3]),
        connectors_by_id={
            7: type("C", (), {"host_parameters": FakeParamsSet([conn_p])})(),
        },
    )
    monkeypatch.setattr(ex, "get_device", lambda project, name="": device)
    return device


def test_device_params_list_and_connector_scope(paramsdev):
    out = ex.cmd_device_params(None, {"op": "list"})
    assert out["device"] == "PFC200_2ETH_RS"
    assert out["count"] == 3
    names = [p["name"] for p in out["parameters"]]
    assert names == ["Board.Temp", "Board.Baudrate", "Net.IP"]
    assert out["parameters"][0]["id"] == "1"
    assert out["parameters"][0]["section"] == "10: General"

    out = ex.cmd_device_params(None, {"op": "list", "connector_id": 7})
    assert out["count"] == 1 and out["parameters"][0]["name"] == "Conn.Param"


def test_device_params_read_offline_online_by_name_and_id(paramsdev):
    out = ex.cmd_device_params(None, {"op": "read", "name": "Board.Temp"})
    assert out["id"] == "1"
    assert out["elements"][0]["identifier"] == "value"
    assert out["elements"][0]["value"] == "31"
    assert "online_value" not in out["elements"][0]

    out = ex.cmd_device_params(
        None, {"op": "read", "id": 2, "online": True, "timeout": 500}
    )
    assert out["elements"][0]["online_value"] == "9600"
    # enum metadata surfaces
    assert out["elements"][0]["allowed_values"] == ["9600", "19200"]

    with pytest.raises(RuntimeError, match="no parameter named"):
        ex.cmd_device_params(None, {"op": "read", "name": "Board.Ghost"})
    with pytest.raises(ToolError, match="name or id required"):
        srv.ecockpit_device_params(op="read")


def test_device_params_write_gate_plain_enum_and_element_target(paramsdev):
    with pytest.raises(RuntimeError, match="REFUSED"):
        ex.cmd_device_params(None, {"op": "write", "name": "Board.Temp", "value": "40"})

    out = ex.cmd_device_params(
        None, {"op": "write", "name": "Board.Temp", "value": "40", "confirm": True}
    )
    assert out["ok"] is True
    assert out["elements"][0]["written"] == "40"
    assert out["elements"][0]["readback"] == "40"

    # enum: invalid value -> engine-side RuntimeError listing allowed
    with pytest.raises(RuntimeError, match="not in allowed values"):
        ex.cmd_device_params(
            None,
            {"op": "write", "name": "Board.Baudrate", "value": "1200", "confirm": True},
        )
    # enum: valid value writes the MATCHING enum object
    out = ex.cmd_device_params(
        None,
        {"op": "write", "name": "Board.Baudrate", "value": "19200", "confirm": True},
    )
    assert out["ok"] is True


def test_device_params_write_element_target_via_device(monkeypatch):
    two_el = FakeParamsParam(4, "Multi", [FakeParamEl("a"), FakeParamEl("b")])
    device = FakeParamsDevice(FakeParamsSet([two_el]))
    monkeypatch.setattr(ex, "get_device", lambda project, name="": device)

    out = ex.cmd_device_params(
        None,
        {"op": "write", "id": 4, "element": "b", "value": "9", "confirm": True},
    )
    assert out["ok"] is True
    assert two_el._elements[0].writes == []  # 'a' untouched
    assert two_el._elements[1].writes == ["9"]

    with pytest.raises(RuntimeError, match="no parameter element matched"):
        ex.cmd_device_params(
            None,
            {"op": "write", "id": 4, "element": "zz", "value": "1", "confirm": True},
        )


# --------------------------------------------------------------------------
# MCP-tool wrappers: confirm gates + argument pass-through (offline)
# --------------------------------------------------------------------------
def test_server_tree_task_params_refusals():
    for call in (
        lambda: srv.ecockpit_tree(op="remove", name="Pou_Main"),
        lambda: srv.ecockpit_task_create(application="App", name="T"),
        lambda: srv.ecockpit_device_params(op="write", name="P", value="1"),
    ):
        out = json.loads(call())
        assert out["refused"] is True
        assert "confirm=true" in out["error"]


def test_server_tree_arg_passthrough_and_validation(monkeypatch):
    captured = {}

    def fake_run(cmd, args=None, timeout_s=None):
        captured["cmd"] = cmd
        captured["args"] = args
        return {"ok": True}

    monkeypatch.setattr(srv, "_run", fake_run)
    srv.ecockpit_tree(op="list", name="Main", limit=5)
    assert captured["cmd"] == "tree"
    assert captured["args"] == {
        "op": "list",
        "name": "Main",
        "recursive": True,
        "limit": 5,
    }
    srv.ecockpit_tree(op="remove", name="Pou_Main", confirm=True)
    assert captured["args"] == {"op": "remove", "name": "Pou_Main", "confirm": True}
    with pytest.raises(ToolError, match="list\\|rename"):
        srv.ecockpit_tree(op="explode")


def test_server_task_create_arg_passthrough(monkeypatch):
    captured = {}

    def fake_run(cmd, args=None, timeout_s=None):
        captured["cmd"] = cmd
        captured["args"] = args
        return {"ok": True}

    monkeypatch.setattr(srv, "_run", fake_run)
    srv.ecockpit_task_create(
        application="App",
        name="Monitor",
        kind="cyclic",
        interval="50",
        pous=["PLC_PRG"],
        confirm=True,
    )
    assert captured["cmd"] == "task_config"
    args = captured["args"]
    assert args["op"] == "create_task"
    assert args["application"] == "App"
    assert args["name"] == "Monitor"
    assert args["kind"] == "cyclic"
    assert args["interval"] == "50"
    assert args["pous"] == ["PLC_PRG"]
    assert args["confirm"] is True
    assert args["watchdog_enabled"] is False
    with pytest.raises(ToolError, match="name"):
        srv.ecockpit_task_create(application="App", name="")


def test_server_device_params_arg_passthrough(monkeypatch):
    captured = {}

    def fake_run(cmd, args=None, timeout_s=None):
        captured["cmd"] = cmd
        captured["args"] = args
        return {"ok": True}

    monkeypatch.setattr(srv, "_run", fake_run)
    srv.ecockpit_device_params(op="read", id=2, online=True, timeout=500)
    assert captured["cmd"] == "device_params"
    assert captured["args"] == {"op": "read", "timeout": 500, "id": 2, "online": True}

    srv.ecockpit_device_params(
        op="write", name="Board.Temp", value="40", element="value", confirm=True
    )
    args = captured["args"]
    assert args["op"] == "write" and args["value"] == "40"
    assert args["element"] == "value" and args["confirm"] is True

    with pytest.raises(ToolError, match="name or id required"):
        srv.ecockpit_device_params(op="write", value="1")
    with pytest.raises(ToolError, match="value required"):
        srv.ecockpit_device_params(op="write", name="P")
