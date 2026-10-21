# -*- coding: utf-8 -*-
"""CI test: the MCP server module imports cleanly under plain CPython.

This is the offline half of ecockpit-mcp - it must never depend on
e!COCKPIT ScriptEngine globals, so importing it in a bare interpreter is a
real regression gate (the two ScriptEngine scripts are covered by their
guarded stand-in blocks instead).
"""

import ecockpit_mcp
from ecockpit_mcp import server as srv


def test_package_version():
    assert ecockpit_mcp.__version__


def test_server_module_imports():
    assert srv.mcp is not None


def test_expected_tools_registered():
    for name in ("ecockpit_server_status", "ecockpit_build", "ecockpit_read",
                 "ecockpit_write", "ecockpit_download",
                 "ecockpit_login", "ecockpit_source_download",
                 "ecockpit_prepared_values",
                 "ecockpit_plc_ls", "ecockpit_plc_get", "ecockpit_plc_put",
                 "ecockpit_plc_rm", "ecockpit_upload_source",
                 "ecockpit_libs", "ecockpit_lib_find",
                 "ecockpit_lib_install", "ecockpit_lib_uninstall",
                 "ecockpit_repos", "ecockpit_repo_move"):
        assert hasattr(srv, name), "missing tool function: " + name


def test_save_archive_tool_absent():
    # e!COCKPIT 11.3.1.0 NREs in every save_archive binding (retested
    # with a saved, named project; see docs/SCRIPTENGINE_API.md) - the
    # tool must stay unregistered so nobody can call it;
    # ecockpit_snapshot is the backup path.
    assert not hasattr(srv, "ecockpit_save_archive")
