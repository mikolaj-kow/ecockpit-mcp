# -*- coding: utf-8 -*-
"""MCP wire-envelope contract: results are TEXT-ONLY, plus the PLC status
section of ecockpit_server_status.

Every tool returns a JSON string with structured_output=False, so the SDK
must ship exactly one TextContent and NO structuredContent / outputSchema.
Before the fix (-> str + SDK 2.3.0 auto-wrap of primitive returns) clients
saw every payload twice: the text block plus structuredContent
{"result": "<the same JSON, escaped as a string>"}.

The older tests call the tool functions directly, which bypasses
FuncMetadata.convert_result - that is why the doubling was never caught.
These tests go through the real envelope conversion (MCPServer.call_tool).
"""

import asyncio
import json
import os
import socket
import time

os.environ["ECP_NO_RUN"] = "1"

import pytest

from mcp.types import TextContent

from ecockpit_mcp import server as srv


@pytest.fixture(autouse=True)
def isolated_work_dir(monkeypatch, tmp_path):
    # status reads the work dir (result file, PLC sidecar, TCP probe):
    # keep these tests hermetic, never touch the live tmp/
    monkeypatch.setattr(srv.CFG, "work_dir", str(tmp_path))


# ---------------------------------------------------------------------------
# wire envelope
# ---------------------------------------------------------------------------


def test_no_tool_advertises_structured_output():
    tools = srv.mcp._tool_manager._tools
    assert len(tools) == 54
    offenders = sorted(
        name for name, tool in tools.items() if tool.output_schema is not None
    )
    assert offenders == []


def test_wire_result_is_text_only():
    result = asyncio.run(srv.mcp.call_tool("ecockpit_server_status", {}))
    assert result.structured_content is None
    assert len(result.content) == 1
    assert isinstance(result.content[0], TextContent)
    payload = json.loads(result.content[0].text)
    assert "armed" in payload
    assert "plc" in payload


# ---------------------------------------------------------------------------
# ecockpit_server_status "plc" section
# ---------------------------------------------------------------------------


def _write_sidecar(work_dir, entry):
    path = os.path.join(work_dir, "ecockpit_plc_state.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(entry, fh)
    return path


def test_status_reports_last_attempt_without_probe(tmp_path):
    _write_sidecar(
        str(tmp_path),
        {
            "online": False,
            "ts": time.time() - 42,
            "detail": "login(Try) failed: Exception: Login failed",
        },
    )
    payload = json.loads(srv.ecockpit_server_status())
    last = payload["plc"]["last_attempt"]
    assert last["online"] is False
    assert "Login failed" in last["detail"]
    assert 40 <= last["age_s"] <= 45
    # no ip recorded yet -> no probe, not a failed probe
    assert payload["plc"]["probe"] is None


def test_status_without_sidecar_reports_nulls(tmp_path):
    payload = json.loads(srv.ecockpit_server_status())
    assert payload["plc"] == {"last_attempt": None, "probe": None}


def test_status_probe_online(monkeypatch, tmp_path):
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    try:
        monkeypatch.setattr(srv.CFG, "plc_probe_port", port)
        _write_sidecar(
            str(tmp_path),
            {"online": True, "ts": time.time(), "ip": "127.0.0.1",
             "detail": "logged in"},
        )
        payload = json.loads(srv.ecockpit_server_status())
    finally:
        listener.close()
    probe = payload["plc"]["probe"]
    assert probe["online"] is True
    assert probe["target"] == "127.0.0.1:{p}".format(p=port)
    assert isinstance(probe["ms"], int)


def test_status_probe_offline(monkeypatch, tmp_path):
    # bind, note the port, close -> connect is refused immediately
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    monkeypatch.setattr(srv.CFG, "plc_probe_port", port)
    _write_sidecar(
        str(tmp_path),
        {"online": False, "ts": time.time(), "ip": "127.0.0.1",
         "detail": "login failed"},
    )
    payload = json.loads(srv.ecockpit_server_status())
    probe = payload["plc"]["probe"]
    assert probe["online"] is False
    assert probe["target"] == "127.0.0.1:{p}".format(p=port)
    assert probe["error"]
