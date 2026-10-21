# ScriptEngine CHM — documented but NOT exposed

Inventory of the ScriptEngine API (from the shipped `ScriptEngine.chm`,
decompiled + parsed) **not** reachable through the ecockpit-mcp tools. The
CHM documents **217 interfaces, ~580 methods and ~830 properties**; this
toolset deliberately exposes a task-oriented subset (~45 members via 24 MCP
tools). Verified-live notes come from `ecockpit_probe` runs; everything
else is CHM-documented but not yet tried in this e!COCKPIT build.

Regenerate the audit (needs the CHM extracted to `tmp/chm_decomp/`):
`7z x "ScriptEngine.chm" -otmp/chm_decomp` then `python tmp/chm_audit.py`.

## Exposed (for orientation)

- Build/compile: `app.build()`, `app.rebuild()`, `app.clean()` (probe),
  `create_boot_application(path)` → `ecockpit_build/rebuild/compile/import`
- Project: `import_xml(reporter, path)`, `export_xml(reporter, objects,
  path, recursive)`, `save()`, `save_as`, `save_archive(path)`, `path`,
  `find`, `get_device`, `devices`, `active_application`,
  `e_projects.open_project/primary`
- Online: `read_value`, `set_prepared_value` + `write/force_prepared_values`
  + `unforce_all_values`, `create_boot_application()`, `start`, `stop`,
  `application_state`/`operation_state`/`is_logged_in`, `connect`/
  `disconnect`/`forced_disconnect`, PLC file ops (`ls/get/put/rm`),
  `upload_source`
- System/messages: `write_message`, `delay`, `get_messages`,
  `get_message_objects`, `get_message_categories`,
  `get_message_category_description`, `process_messageloop`,
  `clear_messages`

## Not exposed — by capability

### Online change control (IScriptOnlineApplication.login)
`login(OnlineChangeOption, delete_foreign_apps)` — explicit online-change
mode. The highest-value remaining candidate: would let a build land on the
running PLC without a full download. `source_download()` also unexposed
(writes the source project to the PLC; the app download path is covered).

### Prepared-value extras (IScriptOnlineApplication)
`get_prepared_value`, `get_prepared_expressions` (readback of staged
values — the write tool verifies via `read_value` instead),
`get_forced_expressions` (list currently forced variables).

### Device management (IScriptOnlineDevice2/5, WAGO IEScriptDevice)
`create_user_management`, `current_logged_on_username`, `connected`,
`activate_license`, and on the WAGO side `add_module`/`delete_module`,
`get_module`, `read_parameter`/`read_parameter_list`/`write_parameter`,
`set_io_mapping`, `update` — scripted hardware/parameter configuration.

### Project plumbing (IScriptProject(s)2/3/4)
`close()`, `create`, `get_by_path`, `open_archive` are exposed but
`convert` (project conversion) and `create_new_project_from_template`
are not; `IScriptProject3.login/logout` (project-level login); `dirty`,
`compiler_version` properties. `save_archive` has NO MCP tool — the
engine NREs in every binding in this build (see SCRIPTENGINE_API.md);
the executor keeps a self-healing probe ladder, `ecockpit_snapshot` is
the backup path.

### Visualization / device tree / build properties
`IScriptBuildProperties` (compiler_defines, link_always,
exclude_from_build, …), symbol config creation, device connectors,
categories — deep object-model areas; use `ecockpit_probe` to explore
live if needed.

## Not in this build (probed live — do not retry)

`system.get_message_log`, `system.get_messages_count`,
`project.get_active_application`, `project/Application.get_build`,
`application.check_all`, `Application.activate_application`,
`project.get_name`. See SCRIPTENGINE_API.md for details.
