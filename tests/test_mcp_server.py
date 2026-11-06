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
    for name in (
        "server_status",
        "build",
        "read",
        "write",
        "download",
        "login",
        "source_download",
        "prepared_values",
        "plc_ls",
        "plc_get",
        "plc_put",
        "plc_rm",
        "plc_fs_ops",
        "upload_source",
        "libs",
        "lib_find",
        "lib_install",
        "lib_uninstall",
        "repos",
        "repo_move",
        "repo_add",
        "repo_remove",
        "repo_update",
        "tree",
        "object_create",
        "textual_read",
        "textual_write",
        "task_config",
        "task_create",
        "symbol_config_variables",
        "symbol_config_create",
        "symbol_config_options",
        "device_scan",
        "device_comm",
        "device_params",
        "app_status",
        "app_control",
        "project_control",
        "system",
        "system_ui",
        "device_info",
        "device_io",
    ):
        assert hasattr(srv, name), "missing tool function: " + name


def test_save_archive_tool_absent():
    # e!COCKPIT 11.3.1.0 NREs in every save_archive binding (retested
    # with a saved, named project; see docs/SCRIPTENGINE_API.md) - the
    # tool must stay unregistered so nobody can call it;
    # snapshot is the backup path.
    assert not hasattr(srv, "save_archive")
