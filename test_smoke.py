# -*- coding: utf-8 -*-
"""Smoke client for the ecockpit MCP server (dev tool).

Usage:
    .venv/Scripts/python.exe test_smoke.py [url]

Checks: initialize, tools/list, server_status, download refusal (no PLC
touch), and - when the in-instance command server is armed - a full build
with structured diagnostics. Not part of the server; run against a live
server.
"""
import asyncio
import io
import json
import os
import sys

from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

URL = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:9777/mcp"


def _token():
    """ECP_MCP_TOKEN from env or the project .env."""
    tok = os.environ.get("ECP_MCP_TOKEN", "")
    if tok:
        return tok
    try:
        with io.open(os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                  ".env"), "r", encoding="utf-8") as fh:
            for raw in fh:
                line = raw.strip()
                if line.startswith("ECP_MCP_TOKEN="):
                    return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _headers():
    tok = _token()
    return {"Authorization": "Bearer " + tok} if tok else {}


async def main():
    import httpx2
    # The build POST blocks for the whole command - the httpx2 default
    # read timeout (~30 s) kills long tool calls. Outlast the largest.
    async with streamable_http_client(
            URL,
            http_client=httpx2.AsyncClient(
                headers=_headers(), timeout=httpx2.Timeout(905.0))) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = sorted(t.name for t in tools.tools)
            print("[smoke] tools ({}): {}".format(len(names), ", ".join(names)))

            res = await session.call_tool("ecockpit_server_status", {})
            status = json.loads(res.content[0].text)
            print("[smoke] armed={} heartbeat_age_s={}".format(
                status.get("armed"), status.get("heartbeat_age_s")))

            res = await session.call_tool("ecockpit_download", {})
            refused = json.loads(res.content[0].text)
            print("[smoke] download refused without confirm: {}".format(
                refused.get("refused")))

            res = await session.call_tool("ecockpit_write",
                                          {"values": {"GVL.x": True}})
            refused = json.loads(res.content[0].text)
            print("[smoke] write refused without confirm: {}".format(
                refused.get("refused")))

            if not status.get("armed"):
                print("[smoke] server not armed - skipping build test")
                return

            res = await session.call_tool("ecockpit_clear_messages", {})
            print("[smoke] clear_messages ok={}".format(
                json.loads(res.content[0].text).get("ok")))

            try:
                res = await session.call_tool("ecockpit_state", {})
                st = json.loads(res.content[0].text)
                print("[smoke] state ok={} value={}".format(
                    st.get("ok"), st.get("value")))
            except Exception as exc:
                print("[smoke] state failed (PLC reachable?): {e}".format(
                    e=str(exc)[:80]))
            print("[smoke] calling ecockpit_build (this compiles, ~30-60 s)...")
            res = await session.call_tool("ecockpit_build", {"timeout_s": 900})
            data = json.loads(res.content[0].text)
            print("[smoke] build ok={} summary={!r} errors={} warnings={}".format(
                data.get("ok"), data.get("summary_line"),
                data.get("errors"), data.get("warnings")))
            harvest = data.get("harvest") or []
            print("[smoke] harvest lines: {}".format(len(harvest)))
            for h in harvest[:5]:
                print("    " + h[:140])
            tail = data.get("log_tail") or []
            if tail:
                print("[smoke] log_tail present ({} lines)".format(len(tail)))


if __name__ == "__main__":
    asyncio.run(main())
