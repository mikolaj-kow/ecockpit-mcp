# -*- coding: utf-8 -*-
"""Transport-UX fixes from HANDOFF 2026-11-03, pinned offline:

- relay-tool timeouts and an unarmed relay come back as ok:false DATA
  (with cmd_id / hint) instead of exceptions the SDK hides behind
  "Error executing tool <name>" (sync via _run, async via _run_async),
- server_status surfaces queued + in_flight commands, so a
  client that timed out or reconnected can still find its command,
- the http daemon's loop exception handler silences the benign
  ConnectionResetError teardown spam without touching real errors.
"""

import asyncio
import inspect
import json
import os
import time

os.environ["ECP_NO_RUN"] = "1"

import pytest

from ecockpit_mcp import server as srv


@pytest.fixture(autouse=True)
def isolated_work_dir(monkeypatch, tmp_path):
    # status/_run read the work dir; keep these tests hermetic, never
    # touch the live tmp/
    monkeypatch.setattr(srv.CFG, "work_dir", str(tmp_path))


def _enriched_timeout(cmd="download", rid="20261103-165745-12345"):
    return srv.CommandTimeoutError(
        "no result for command '{}' (id {}) after 300 s - test".format(cmd, rid),
        cmd_id=rid,
        cmd=cmd,
        timeout_s=300,
    )


async def _call_tool_body(name):
    """Invoke a tool function's body (bypasses the mcp decorator), the
    same direct-call shape the wire-envelope tests use."""
    fn = getattr(srv, name)
    if inspect.iscoroutinefunction(fn):
        return await fn()
    return fn()


# ---------------------------------------------------------------------------
# timeout-as-data (the 300 s download abort scenario)
# ---------------------------------------------------------------------------


def test_sync_run_converts_timeout_to_data(monkeypatch):
    def raise_timeout(cmd, args, timeout_s):
        raise _enriched_timeout()

    monkeypatch.setattr(srv, "_send_command", raise_timeout)
    payload = json.loads(asyncio.run(_call_tool_body("ecockpit_status")))
    assert payload["ok"] is False
    assert payload["error"] == "timeout"
    assert payload["cmd_id"] == "20261103-165745-12345"
    assert payload["cmd"] == "download"
    assert payload["timeout_s"] == 300
    assert "ecockpit_server_status" in payload["hint"]
    assert "TIMEOUT" in payload["summary_line"]


def test_sync_run_converts_not_armed_to_data(monkeypatch):
    def raise_not_armed(cmd, args, timeout_s):
        raise srv.NotArmedError("no fresh heartbeat (age 346 s) - test")

    monkeypatch.setattr(srv, "_send_command", raise_not_armed)
    payload = json.loads(asyncio.run(_call_tool_body("ecockpit_status")))
    assert payload["ok"] is False
    assert payload["error"] == "not_armed"
    assert "relay.py" in payload["hint"]
    assert "NOT ARMED" in payload["summary_line"]


def test_async_run_converts_timeout_to_data(monkeypatch):
    async def raise_timeout(cmd, args, timeout_s, ctx=None, **kw):
        raise _enriched_timeout()

    monkeypatch.setattr(srv, "_send_command_async", raise_timeout)
    payload = json.loads(asyncio.run(srv.ecockpit_build()))
    assert payload["ok"] is False
    assert payload["error"] == "timeout"
    assert payload["cmd_id"] == "20261103-165745-12345"
    assert payload["cmd"] == "download"


# ---------------------------------------------------------------------------
# server_status: queued + in_flight
# ---------------------------------------------------------------------------


def test_status_without_files_reports_null_queue_and_flight():
    payload = json.loads(srv.server_status())
    assert payload["queued"] is None
    assert payload["in_flight"] is None


def test_status_reports_queued_command():
    with open(srv.CFG.cmd_file, "w", encoding="utf-8") as fh:
        json.dump({"id": "q-1", "cmd": "download", "args": {}}, fh)
    payload = json.loads(srv.server_status())
    assert payload["queued"] == {"id": "q-1", "cmd": "download"}


def test_status_reports_in_flight_with_age():
    with open(srv.CFG.current_file, "w", encoding="utf-8") as fh:
        json.dump({"id": "f-1", "cmd": "download", "ts": time.time() - 10}, fh)
    payload = json.loads(srv.server_status())
    flight = payload["in_flight"]
    assert flight["id"] == "f-1"
    assert flight["cmd"] == "download"
    assert 9 <= flight["age_s"] <= 12


def test_status_survives_malformed_marker_files():
    with open(srv.CFG.current_file, "w", encoding="utf-8") as fh:
        fh.write("{not json")
    with open(srv.CFG.cmd_file, "w", encoding="utf-8") as fh:
        fh.write("[]")
    payload = json.loads(srv.server_status())
    assert payload["queued"] is None
    assert payload["in_flight"] is None


# ---------------------------------------------------------------------------
# _quiet_peer_reset: benign reset filtered, real errors delegated
# ---------------------------------------------------------------------------


class _FakeLoop:
    def __init__(self):
        self.delegated = []

    def default_exception_handler(self, context):
        self.delegated.append(context)


def test_quiet_peer_reset_swallows_resets(capsys):
    loop = _FakeLoop()
    srv._quiet_peer_reset(loop, {"exception": ConnectionResetError(10054, "reset")})
    srv._quiet_peer_reset(loop, {"exception": ConnectionAbortedError(10053, "abort")})
    assert loop.delegated == []  # nothing delegated
    out = capsys.readouterr().out
    assert out.count("peer dropped a connection") == 2  # one line each
    assert "Traceback" not in out


def test_quiet_peer_reset_delegates_other_exceptions():
    loop = _FakeLoop()
    context = {"exception": KeyError("real problem"), "message": "x"}
    srv._quiet_peer_reset(loop, context)
    assert loop.delegated == [context]


def test_quiet_peer_reset_handles_missing_exception():
    loop = _FakeLoop()
    context = {"message": "no exception key"}
    srv._quiet_peer_reset(loop, context)
    assert loop.delegated == [context]
