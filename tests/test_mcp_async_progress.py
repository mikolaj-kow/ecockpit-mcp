# -*- coding: utf-8 -*-
"""Offline tests for the async command path (progress notifications).

_send_command_async is the twin of the blocking _send_command: it waits
on the event loop and emits MCP progress notifications while the engine
works. Here a fake engine thread answers the file protocol from a tmp
work dir, so the progress behaviour is testable without e!COCKPIT.
"""

import asyncio
import json
import os
import threading
import time

import pytest

from ecockpit_mcp import server as srv


class RecordingCtx:
    """Stand-in for mcp Context - records report_progress calls."""

    def __init__(self):
        self.events = []

    async def report_progress(self, progress, total=None, message=None):
        self.events.append((progress, total, message))


class FakeEngine(threading.Thread):
    """Answers one file-protocol request after a simulated work delay."""

    def __init__(self, cfg, delay_s):
        super().__init__(daemon=True)
        self.cfg = cfg
        self.delay_s = delay_s

    def run(self):
        deadline = time.time() + 5
        while time.time() < deadline:
            if os.path.isfile(self.cfg.cmd_file):
                break
            time.sleep(0.02)
        else:
            return  # no request arrived - the waiter will time out
        with open(self.cfg.cmd_file, "r", encoding="utf-8") as fh:
            request = json.load(fh)
        time.sleep(self.delay_s)
        with open(self.cfg.result_file, "w", encoding="utf-8") as fh:
            json.dump(
                {"id": request["id"], "cmd": request["cmd"], "ok": True, "value": 42},
                fh,
            )


@pytest.fixture()
def fake_work_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(srv.CFG, "work_dir", str(tmp_path))
    with open(srv.CFG.heartbeat_file, "w", encoding="utf-8"):
        pass  # fresh heartbeat -> _require_heartbeat passes
    return srv.CFG


def test_async_send_reports_progress(fake_work_dir):
    engine = FakeEngine(fake_work_dir, delay_s=0.5)
    engine.start()
    ctx = RecordingCtx()
    try:
        result = asyncio.run(
            srv._send_command_async(
                "build", {}, 10, ctx, poll_s=0.05, progress_every_s=0.1
            )
        )
    finally:
        engine.join()
    assert result["ok"] is True
    assert result["value"] == 42
    # intermediate notifications while the engine worked ...
    assert len(ctx.events) >= 3
    for progress, total, message in ctx.events:
        assert 0 <= progress <= 10
        assert total == 10.0
        assert "'build' running" in message
    # ... monotonically rising, and a final one at completion
    rises = [e[0] for e in ctx.events]
    assert rises == sorted(rises)
    assert rises[-1] >= 0.4  # engine worked 0.5 s before answering


def test_async_send_without_ctx_skips_progress(fake_work_dir):
    engine = FakeEngine(fake_work_dir, delay_s=0.0)
    engine.start()
    try:
        result = asyncio.run(
            srv._send_command_async("status", {}, 10, None, poll_s=0.05)
        )
    finally:
        engine.join()
    assert result["ok"] is True


def test_async_send_times_out(fake_work_dir):
    # nobody answers the request -> TimeoutError against a small timeout
    with pytest.raises(TimeoutError):
        asyncio.run(
            srv._send_command_async(
                "build", {}, 0.3, RecordingCtx(), poll_s=0.05, progress_every_s=0.1
            )
        )


def test_long_tools_are_async_with_progress_param():
    # the long-running set (default timeout >= 300 s) must be async and
    # take ctx; everything else stays sync so short ops keep their
    # direct-call shape.
    long_tools = (
        "ecockpit_build",
        "ecockpit_import",
        "ecockpit_compile",
        "ecockpit_rebuild",
        "ecockpit_export",
        "ecockpit_snapshot",
        "ecockpit_system_ui",
        "ecockpit_plc_ls",
        "ecockpit_plc_get",
        "ecockpit_plc_put",
        "ecockpit_plc_rm",
        "ecockpit_plc_fs_ops",
        "ecockpit_upload_source",
        "ecockpit_download",
        "ecockpit_source_download",
        "ecockpit_symbol_config_variables",
        "ecockpit_symbol_config_options",
        "ecockpit_device_scan",
        "ecockpit_device_io",
        "ecockpit_app_control",
        "ecockpit_project_control",
        "ecockpit_object_create",
    )
    import inspect

    for name in long_tools:
        fn = getattr(srv, name)
        assert inspect.iscoroutinefunction(fn), name + " must be async"
        assert "ctx" in inspect.signature(fn).parameters, name
    for name in (
        "ecockpit_server_status",
        "ecockpit_read",
        "ecockpit_write",
        "ecockpit_state",
        "ecockpit_device_info",
    ):
        assert not inspect.iscoroutinefunction(getattr(srv, name)), (
            name + " should stay sync (short op)"
        )


def test_snapshot_tool_binds_ctx_not_timeout(fake_work_dir):
    # LIVE 2026-10-23 regression: ecockpit_snapshot called
    # _run_async("snapshot", args, ctx) positionally, so the Context
    # landed in timeout_s. The command still reached the engine (the
    # dispatch precedes the deadline math) but the wrapper raised
    # TypeError float + Context - the MCP caller saw an exception while
    # the snapshot actually ran engine-side. Offline tests missed it
    # because ctx=None silently falls back to the default timeout.
    engine = FakeEngine(fake_work_dir, delay_s=0.0)
    engine.start()
    ctx = RecordingCtx()
    try:
        result = json.loads(asyncio.run(srv.ecockpit_snapshot(ctx=ctx)))
    finally:
        engine.join()
    assert result["ok"] is True
    assert result["cmd"] == "snapshot"
