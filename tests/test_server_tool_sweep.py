# -*- coding: utf-8 -*-
"""Wrapper sweep: every registered MCP tool is called offline with
minimal schema-derived arguments, against a stubbed engine client.

This pins, for all 54 tools at once:
- the confirm-gate refusals return refused JSON (never raise, never
  touch the engine),
- the arg-assembly bodies produce the documented command/args shapes
  (captured against stub _run / _run_async),
- every wrapper returns a JSON object - a wrapper that starts raising
  TypeError/KeyError after a refactor fails here, not on a live PLC.
"""

import asyncio
import inspect
import json
import os

os.environ["ECP_NO_RUN"] = "1"

import pytest

from ecockpit_mcp import server as srv

# tools whose MINIMAL call (required args only, no confirm) hits the
# confirm-gate refusal instead of arg assembly
GATED_ON_MINIMAL_CALL = (
    "ecockpit_write",
    "ecockpit_download",
    "ecockpit_login",
    "ecockpit_source_download",
    "ecockpit_stop",
    "ecockpit_system_ui",
    "ecockpit_object_create",
    "ecockpit_textual_write",
    "ecockpit_task_create",
    "ecockpit_symbol_config_create",
    "ecockpit_lib_uninstall",
    "ecockpit_repo_add",
    "ecockpit_repo_remove",
    "ecockpit_repo_update",
    "ecockpit_plc_put",
    "ecockpit_plc_rm",
)

# required-arg overrides where the schema alone cannot pick a valid value
ARG_OVERRIDES = {
    "ecockpit_plc_fs_ops": {"op": "ls"},
    "ecockpit_app_control": {"op": "status"},
    "ecockpit_project_control": {"op": "info"},
    "ecockpit_symbol_config_options": {"op": "config"},
    "ecockpit_device_comm": {"op": "get"},
    "ecockpit_device_info": {"op": "identification"},
    "ecockpit_device_scan": {"op": "gateways"},
    # system_ui needs op+method so the confirm=true path reaches
    # arg assembly (without confirm the gate refuses first)
    "ecockpit_system_ui": {"op": "ui", "method": "info"},
    # repo_update: name alone is not a valid minimal call
    "ecockpit_repo_update": {"new_name": "y"},
    # object_create with confirm=true needs kind AND name
    "ecockpit_object_create": {"kind": "gvl", "name": "NewGVL"},
}

_SAMPLE_VALUES = {
    "string": "x",
    "integer": 1,
    "number": 1,
    "boolean": False,
    "object": {"GVL.x": True},
    "array": ["x"],
}


def _minimal_args(tool):
    schema = tool.parameters
    overrides = ARG_OVERRIDES.get(tool.name, {})
    props = schema.get("properties", {})
    args = {}
    for name in schema.get("required", []):
        if name in overrides:
            args[name] = overrides[name]
            continue
        prop = props.get(name, {})
        args[name] = _SAMPLE_VALUES.get(prop.get("type", "string"), "x")
    args.update({k: v for k, v in overrides.items() if k not in args})
    return args


async def _call(fn, args):
    if inspect.iscoroutinefunction(fn):
        return await fn(**args)
    return fn(**args)


class EngineRecorder:
    def __init__(self):
        self.calls = []

    def command_args(self, name):
        return [a for (_k, cmd, a) in self.calls if cmd == name]


@pytest.fixture()
def stub_engine(monkeypatch):
    rec = EngineRecorder()
    calls = rec.calls

    def fake_run(cmd, args=None, timeout_s=None):
        calls.append(("sync", cmd, args))
        return {"ok": True, "cmd": cmd}

    async def fake_run_async(cmd, args=None, timeout_s=None, ctx=None, **kw):
        calls.append(("async", cmd, args))
        return {"ok": True, "cmd": cmd}

    monkeypatch.setattr(srv, "_run", fake_run)
    monkeypatch.setattr(srv, "_run_async", fake_run_async)
    return rec


def test_every_tool_returns_json_and_is_reached(stub_engine):
    tools = srv.mcp._tool_manager._tools
    assert len(tools) == 54
    refused, assembled = [], []
    for name, tool in sorted(tools.items()):
        args = _minimal_args(tool)
        fn = getattr(srv, name)
        payload = json.loads(asyncio.run(_call(fn, args)))
        assert isinstance(payload, dict), name
        if payload.get("refused") is True:
            refused.append(name)
            assert "confirm=true" in payload["error"], name
        else:
            assembled.append(name)
            if name == "ecockpit_server_status":
                assert "armed" in payload  # local shape, no engine call
            else:
                assert payload.get("ok") is True, name

    assert sorted(refused) == sorted(GATED_ON_MINIMAL_CALL)
    # server_status is local-file only (no engine call); everything else
    # reached the stub client exactly once
    reached = {cmd for (_k, cmd, _a) in stub_engine.calls}
    assert "status" in reached  # ecockpit_status
    assert len(stub_engine.calls) == len(assembled) - 1  # - server_status


def test_gated_tools_pass_confirm_to_engine_after_confirm(stub_engine):
    tools = srv.mcp._tool_manager._tools
    for name in GATED_ON_MINIMAL_CALL:
        args = _minimal_args(tools[name])
        args["confirm"] = True
        fn = getattr(srv, name)
        payload = json.loads(asyncio.run(_call(fn, args)))
        assert payload.get("refused") is None, name
        last_cmd, last_args = stub_engine.calls[-1][1], stub_engine.calls[-1][2]
        assert last_args and last_args.get("confirm") is True, (
            name + " must forward confirm=true to the engine (sent " + last_cmd + ")"
        )


def test_sweep_args_use_schema_types(stub_engine):
    # spot-check the generator honours the schema for a tricky tool
    tool = srv.mcp._tool_manager._tools["ecockpit_write"]
    assert _minimal_args(tool) == {"values": {"GVL.x": True}}
    tool = srv.mcp._tool_manager._tools["ecockpit_plc_fs_ops"]
    assert _minimal_args(tool) == {"op": "ls", "remote": "x"}
