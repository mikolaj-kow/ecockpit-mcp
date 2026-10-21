# -*- coding: utf-8 -*-
"""CI test: the MCP server module imports cleanly under plain CPython.

This is the offline half of ecockpit-mcp - it must never depend on
e!COCKPIT ScriptEngine globals, so importing it in a bare interpreter is a
real regression gate (the two ScriptEngine scripts are covered by their
guarded stand-in blocks instead).
"""

import ecockpit_mcp_server as srv


def test_server_module_imports():
    assert srv.mcp is not None


def test_expected_tools_registered():
    for name in ("ecockpit_server_status", "ecockpit_build", "ecockpit_read",
                 "ecockpit_write", "ecockpit_download"):
        assert hasattr(srv, name), "missing tool function: " + name
