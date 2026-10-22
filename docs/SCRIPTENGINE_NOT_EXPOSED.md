# ScriptEngine — documented but NOT exposed (contract)

Machine-readable inventory of ScriptEngine capabilities that the ScriptLib
stubs document but ecockpit-mcp does **not** expose as MCP tools yet.
Exposed surface: [SUMMARY.md](SUMMARY.md) (master table, done + possible),
[SCRIPTENGINE_API.md](SCRIPTENGINE_API.md) (verified reference),
[SCRIPTENGINE_EXPLORATION.md](SCRIPTENGINE_EXPLORATION.md) (how to add
more). Live-test results: [TESTING.md](TESTING.md).

Status: `possible` (stub-documented, unimplemented) · `partial` ·
`broken (live)` (engine defect — see SCRIPTENGINE_API.md / GOTCHAS).

State: the CHM documents ~217 interfaces / ~580 methods / ~830
properties; the ScriptLib stubs refine them; ecockpit-mcp exposes its
task-oriented subset via 54 MCP tools (2026-10-22; plc_fs_ops counts once for its seven ops).

| Capability | Stub chain (file → call) | Status | Notes |
|---|---|---|---|
| Library Manager object (in-project references) | ScriptLibManObject.pyi → `app.get_library_manager()` → `add_library(name\|ManagedLib)` / `add_placeholder(name, resolution)` / `remove_library(name)` / `references[name]` (`.set_redirection`, `.default_resolution`, `.qualified_only`) / `parameters["P"] = expr` | possible | complements the exposed repository curation; nearest-term item |
| Project user management | ScriptUserManagement.pyi → `project.user_management` → `users.create(name)` / `groups.create` / permissions (`set_permission_state`) / `login(username, password)` | possible | sensitive: creates users/ACLs |
| Device user management (offline) | ScriptOnline.pyi (ScriptOnlineDevice) → `create_user_management(load_from_project)` → users/groups/`set_password` → `download()` (PLC-touching) / `store_to_project()` | possible | pre-3.5.16 runtimes; XML round-trip via `load`/`save` |
| Device user management (live, SP16+) | ScriptLiveDeviceUserManagement.pyi → `create_live_user_management()` → `add_user` / `add_group` / `set_user_password` / `set_members_of_group` / `backup(dir, pw)` / `restore(path, pw)` | possible | `connect()` first; `restore()` invalidates the session (reconnect pattern in stub) |
| Device tree authoring | ScriptDeviceObject.pyi → `project.add(name, DeviceID)` / `dev.plug`/`add`/`insert(name, DeviceID)` / `dev.update(DeviceID)` / `dev.unplug()`; ScriptExplicitConnectorObjects.pyi → `connector.add/insert(name, DeviceID)` + `allowed_interfaces_at(index)` | possible | `DeviceID` via injected `DeviceID(type, id, version)`; `unplug` destructive |
| WAGO module + legacy params | ScriptDeviceObject.pyi (WAGO IEScriptDevice) → `add_module` / `delete_module` / `get_module` / `read_parameter(_list)` / `write_parameter` / `set_io_mapping` / `update` | partial | partly superseded by `ecockpit_device_params` (ScriptDeviceParameters set) |
| Project convert / template | ScriptProjects.pyi → `convert(path, output_path, converter, primary)` / `create_new_project_from_template` | possible | V2.3 `.pro`/`.lib` converter GUIDs documented in the stub |
| License activation | ScriptOnline.pyi (ScriptOnlineDevice) → `activate_license(ticket, url, license_names)` | possible | writes a license to the device |
| Gateway add/remove | ScriptOnline.pyi (ScriptGateways) → `add_new_gateway(name, parameters, driver)` / `remove_gateway(gateway)` / `convert_gateway_parameter` | possible | scan/list are exposed (`ecockpit_device_scan`); add/remove mutates CODESYS config |
| Build properties | ScriptObject.pyi → `obj.build_properties.{external, enable_system_call, compiler_defines, link_always, exclude_from_build}` (+ `effectively_excluded_from_build`) | possible | per-object build exclusion |
| Project settings / sourcedownload config | ScriptProject.pyi → `project.project_settings.set_sourcedownload(timing, content, compact, device)` / `available_download_content` | possible | 3.5.12.10; configures what `download_source` packs |
| Text lists | ScriptTextListObject.pyi → `project.create_textlist(name)` / `get_global_textlist()` → `rows.add(id, defaulttext)` / `row.setlanguagetext(lang, text)` / `importfile(csv)` / `export(csv)` | possible | visualization texts |
| Image pools | ScriptImagePoolObject.pyi → `project.create_imagepool(name)` / `get_global_imagepool()` → `images.add(id, path, linktype)` / `remove(id)` | possible | visualization images |
| External files | ScriptExternalFileObject.pyi → `create_external_file_object(path, name, reference_mode, auto_update_mode)` → `get_data(file)` / `update()` | possible | project-embedded files with runtime sync |
| Native import/export | ScriptProject.pyi → `export_native(objects, destination, ...)` / `import_native(filenames, ...)`; ScriptObject.pyi → object-level `export_native`/`import_native` | possible | native (non-PLCopen) format |
| Project compare | ScriptProject.pyi → `compare_to(right_project, flags)` → `get_changed_objects(ObjectDifferences.CONTENT_CHANGED)` | possible | 3.5.12.0; regression diffs |
| Compiled library build | ScriptProject.pyi → `save_as_compiled_library(destination)` / `check_all_pool_objects()` | possible | library projects |
| Runtime system files | ScriptProject.pyi → `generate_runtime_system_files(destination, generate_m4, generate_c)` | possible | niche |
| Archive categories metadata | ScriptLibManObject.pyi (LibManager) → `categories` / `top_level_categories` / `get_category(guid)` | partial | enums injected; tools not needed yet |

## Not in this build (probed live — do not retry)

`system.get_message_log`, `system.get_messages_count`,
`project.get_active_application`, `project/Application.get_build`,
`application.check_all`, `Application.activate_application`,
`project.get_name`. See SCRIPTENGINE_API.md for details.
