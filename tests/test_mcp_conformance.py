# -*- coding: utf-8 -*-
"""MCP-spec conformance checks: the real server driven by a real MCP
client over in-memory streams (the same protocol path stdio/http
serve).

Offline by construction: the relay is unarmed, so reads come back as
ok:false payloads and refusals as data - the checks pin the protocol
SHAPES (initialize, tools/list schemas + annotations, resources,
prompts, progress notifications), not engine behaviour.
"""

import asyncio
import contextlib
import inspect
import json
import os

os.environ["ECP_NO_RUN"] = "1"

import pytest
from mcp.client.session import ClientSession
from mcp.shared.memory import create_client_server_memory_streams

from ecockpit_mcp import server as srv

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@contextlib.asynccontextmanager
async def conformance_session():
    """ClientSession wired to the real ecockpit MCPServer over memory
    streams; the server task is cancelled on exit."""
    async with create_client_server_memory_streams() as (client, server):
        init_opts = srv.mcp._lowlevel_server.create_initialization_options()
        server_task = asyncio.ensure_future(
            srv.mcp._lowlevel_server.run(server[0], server[1], init_opts)
        )
        try:
            async with ClientSession(client[0], client[1]) as session:
                yield session
        finally:
            server_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await server_task


@pytest.fixture()
def fast_not_armed(monkeypatch):
    """Skip the 45 s heartbeat wait - the relay is unarmed in tests."""

    def _raise(*args, **kwargs):
        raise srv.NotArmedError("no fresh heartbeat (age 99999 s) - test")

    monkeypatch.setattr(srv, "_require_heartbeat", _raise)


async def test_initialize_advertises_capabilities():
    async with conformance_session() as session:
        result = await session.initialize()
        assert result.server_info.name == "ecockpit"
        assert "confirm=true" in (result.instructions or "")
        assert result.capabilities.tools is not None
        assert result.capabilities.resources is not None
        assert result.capabilities.prompts is not None


async def test_tools_list_schemas_and_annotations():
    async with conformance_session() as session:
        await session.initialize()
        tools = await session.list_tools()
        names = {t.name for t in tools.tools}
        assert len(names) == 54
        assert "ecockpit_build" in names
        # conformance invariant: every tool annotated destructiveHint
        # carries the confirm gate in its input schema
        destructive = [
            t for t in tools.tools if t.annotations and t.annotations.destructive_hint
        ]
        assert destructive, "the destructive set must be annotated"
        for tool in destructive:
            props = tool.input_schema.get("properties", {})
            assert "confirm" in props, tool.name + " gates on confirm"
            assert props["confirm"].get("type") == "boolean"
        # read-only hints must not gate on confirm
        readonly = [
            t for t in tools.tools if t.annotations and t.annotations.read_only_hint
        ]
        assert readonly, "the read-only set must be annotated"
        for tool in readonly:
            # read-only tools may carry a confirm param for optional
            # mutating ops (ecockpit_tree: rename/remove/move/folder),
            # but the read-only default call must never REQUIRE it
            assert "confirm" not in tool.input_schema.get("required", []), (
                tool.name
            )


async def test_resources_list_and_read_offline(fast_not_armed):
    async with conformance_session() as session:
        await session.initialize()
        resources = await session.list_resources()
        uris = {str(r.uri) for r in resources.resources}
        assert "ecockpit://project/tree" in uris
        assert "ecockpit://messages" in uris
        templates = await session.list_resource_templates()
        assert any("textual/{name}" in str(t.uri_template) for t in templates.resource_templates)

        # an unarmed relay reads back an ok:false payload, not a
        # protocol error - resource reads stay agent-usable offline
        contents = await session.read_resource("ecockpit://messages")
        payload = json.loads(contents.contents[0].text)
        assert payload["ok"] is False
        assert "relay.py" in payload["hint"]

        contents = await session.read_resource("ecockpit://project/textual/MyPou")
        payload = json.loads(contents.contents[0].text)
        assert payload["ok"] is False


async def test_prompts_list_and_get():
    async with conformance_session() as session:
        await session.initialize()
        prompts = await session.list_prompts()
        assert "diagnose-failed-build" in {p.name for p in prompts.prompts}

        failing = json.dumps({"ok": False, "harvest": ["C0072: syntax error in PLC_x"]})
        result = await session.get_prompt(
            "diagnose-failed-build", {"build_result": failing}
        )
        assert len(result.messages) >= 1
        message = result.messages[0]
        assert message.role == "user"
        text = message.content.text
        assert "C0072" in text  # the pasted result is embedded
        assert "IGNORE_CODES" in text  # checklist step 2
        assert "ecockpit_rebuild" in text  # checklist step 3


async def test_call_tool_offline_status_and_refusal_shapes():
    async with conformance_session() as session:
        await session.initialize()
        # harmless local tool: offline-safe JSON blob
        result = await session.call_tool("ecockpit_server_status", {})
        assert result.is_error is False
        status = json.loads(result.content[0].text)
        assert status["armed"] is False

        # a refused destructive call is DATA (isError stays false), so
        # agents can read the refusal instead of a protocol exception
        result = await session.call_tool("ecockpit_download", {})
        assert result.is_error is False
        refusal = json.loads(result.content[0].text)
        assert refusal["refused"] is True
        assert "confirm=true" in refusal["error"]


async def test_progress_notification_end_to_end(monkeypatch, fast_not_armed):
    async def fake_run_async(cmd, args=None, timeout_s=None, ctx=None, **kwargs):
        if ctx is not None:
            await ctx.report_progress(1.0, 2.0, "halfway there")
        return {"id": "fake", "cmd": cmd, "ok": True, "value": None}

    monkeypatch.setattr(srv, "_run_async", fake_run_async)
    events = []

    async def on_progress(progress, total, message):
        events.append((progress, total, message))

    async with conformance_session() as session:
        await session.initialize()
        result = await session.call_tool(
            "ecockpit_build", {}, progress_callback=on_progress
        )
    assert result.is_error is False
    assert (1.0, 2.0, "halfway there") in events


async def test_tool_error_surfaces_as_protocol_error():
    async with conformance_session() as session:
        await session.initialize()
        # invalid arguments reach the tool's own validation and come
        # back as a tool error (isError=true), not a crash
        result = await session.call_tool("ecockpit_read", {"variable": ""})
        assert result.is_error is True
        assert "variable path is required" in result.content[0].text


def _check_anyio_plugin():
    # guard: the anyio pytest plugin must be active, or the anyio-marked
    # async tests above silently no-op
    try:
        import anyio.pytest_plugin  # noqa: F401
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError("anyio pytest plugin missing") from exc


_check_anyio_plugin()
assert inspect.iscoroutinefunction  # keep inspect import meaningful
