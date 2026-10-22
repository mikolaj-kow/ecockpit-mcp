# ScriptEngine function summary — done + possible

Master table of every ScriptEngine capability we inventoried (ScriptLib
stubs + CHM + live). **Machine-readable contract** — columns:

- **Category**: domain (the former section grouping).
- **Function / call chain**: the exact script-side invocation;
  IronPython 2.7 syntax applies.
- **Status**: ✅ implemented · 🟡 possible (stub-documented,
  unimplemented) · ❌ broken (live, engine defect) · 🗑️ removed.
- **Tools**: the MCP invocation (`—` = no tool; internal = used by the
  executor itself).

Details: [SCRIPTENGINE_API.md](SCRIPTENGINE_API.md) (verified),
[SCRIPTENGINE_GOTCHAS.md](SCRIPTENGINE_GOTCHAS.md) (traps, G-nn),
[SCRIPTENGINE_NOT_EXPOSED.md](SCRIPTENGINE_NOT_EXPOSED.md),
[SCRIPTENGINE_EXPLORATION.md](SCRIPTENGINE_EXPLORATION.md) (how to add
more). Live results: [TESTING.md](TESTING.md).

| Category | Function / call chain | Status | Tools |
|---|---|---|---|
| System / messages | `system.write_message(severity, text)` | ✅ implemented (executor logs) | — |
| System / messages | `system.get_message_categories()` | ✅ implemented | ecockpit_messages / ecockpit_clear_messages |
| System / messages | `system.get_message_objects(category, severities)` | ✅ implemented | ecockpit_messages + build harvest |
| System / messages | `system.clear_messages(category)` | ✅ implemented | ecockpit_clear_messages |
| System / messages | `system.delay(ms)` / `process_messageloop()` | ✅ implemented (internal) | — |
| System / messages | `system.prompt_handling` / `prompt_answers` (headless dialog simulation) | 🟡 possible (LogSimplePrompts applied; answers not) | — |
| System / messages | `system.get_message_category_description(category)` | ❌ broken (live): not callable | — |
| System / messages | `system.get_message_log` / `get_messages_count` | ❌ broken (live): absent | — |
| System / messages | `system.progress_start/step/absolute`, `abortable`, `ui.*` | 🟡 possible (ui.* = G-08 wedge hazard) | — |
| System / messages | `system.exit(code)` | 🟡 possible (DESTRUCTIVE: kills e!COCKPIT) | — |
| Project lifecycle | `projects.primary` / `get_by_path(path)` / `all` | ✅ implemented (internal, `get_project`) | ecockpit_status |
| Project lifecycle | `projects.create(path, primary)` | ✅ implemented | ecockpit_project_control op=create |
| Project lifecycle | `projects.open(path, password, primary, update_flags)` | ✅ implemented | ecockpit_project_control op=open |
| Project lifecycle | `projects.open_archive(archive, projectpath, overwrite)` | ✅ implemented | ecockpit_project_control op=open_archive |
| Project lifecycle | `projects.convert(path, output_path, converter, primary)` | 🟡 possible | — |
| Project lifecycle | `project.save()` | ✅ implemented | ecockpit_project_control op=save |
| Project lifecycle | `project.save_as(path, password)` | 🟡 possible (retargets the open project) | — |
| Project lifecycle | `project.save_archive(path, comment, additional_files, *cats)` | ❌ broken (live): NREs in all 6 bindings (G-14) · 🗑️ no tool | — |
| Project lifecycle | `project.close()` (DISCARDS unsaved changes) | ✅ implemented | ecockpit_project_control op=close |
| Project lifecycle | `project.dirty` / `project.path` | ✅ implemented | ecockpit_project_control op=info |
| Project lifecycle | `project.get_project_info()` → company/title/version/author/description | ✅ implemented | ecockpit_project_control info/info_set |
| Project lifecycle | `project.login(username, password)` / `logout()` (project user mgmt) | 🟡 possible | — |
| Project lifecycle | `project.user_management` (users/groups/permissions) | 🟡 possible (sensitive: ACLs) | — |
| Project lifecycle | `project.check_all_pool_objects()` / `save_as_compiled_library(dst)` / `generate_runtime_system_files(...)` | 🟡 possible | — |
| Project lifecycle | `project.compare_to(right, flags)` → object differences | 🟡 possible | — |
| Project lifecycle | `project.project_settings.set_sourcedownload(timing, content, compact, device)` / `available_download_content` | 🟡 possible | — |
| Project lifecycle | `project.enable_integrity_check()` / `disable_encryption()` | 🟡 possible (security-setting change) | — |
| Tree / objects | `project.find(name, recursive)` / `get_children(recursive)` | ✅ implemented | ecockpit_tree op=list |
| Tree / objects | `obj.get_name()` / `guid` / kind markers | ✅ implemented | ecockpit_tree op=list |
| Tree / objects | `obj.rename(new_name)` | ✅ implemented | ecockpit_tree op=rename |
| Tree / objects | `obj.remove()` | ✅ implemented | ecockpit_tree op=remove |
| Tree / objects | `obj.move(parent, index)` | ✅ implemented | ecockpit_tree op=move |
| Tree / objects | `obj.create_folder(name)` / `project.create_folder(name, view)` | ✅ implemented | ecockpit_tree op=folder |
| Tree / objects | `obj.get_signature_crc()` | 🟡 possible | — |
| Tree / objects | `obj.build_properties.*` (exclude_from_build, ...) | 🟡 possible | — |
| Tree / objects | `obj.export_native` / `import_native` | 🟡 possible | — |
| ST authoring | `container.create_pou(name, type=PouType.*, language=ImplementationLanguages.st, return_type)` | ✅ implemented | ecockpit_object_create kind=pou |
| ST authoring | `container.create_gvl(name)` | ✅ implemented | ecockpit_object_create kind=gvl |
| ST authoring | `container.create_dut(name, type=DutType.*, baseType)` | ✅ implemented | ecockpit_object_create kind=dut |
| ST authoring | `container.create_interface(name, baseInterfaces)` | ✅ implemented | ecockpit_object_create kind=interface |
| ST authoring | `pou.create_method/property/action/transition(...)` | ✅ implemented | ecockpit_object_create kind=method/... |
| ST authoring | `obj.textual_declaration` / `textual_implementation` → `.text` / `.replace(new_text=...)` | ✅ implemented | ecockpit_textual_read/write |
| ST authoring | `create_pou(SpecialPouType.*)` (check functions) | 🟡 possible (forces full recompile, kills online change) | — |
| Task config | `app.create_task_configuration()` → `tc.create_task(name)` | ✅ implemented | ecockpit_task_create |
| Task config | `task.kind_of_task` (KindOfTask.Cyclic/...) / `priority` / `interval` / `interval_unit` | ✅ implemented | ecockpit_task_config / ecockpit_task_create |
| Task config | `task.watchdog.{enabled,time,time_unit,sensitivity}` | ✅ implemented | ecockpit_task_create/config |
| Task config | `task.pous.add/insert/replace/remove` | ✅ implemented | ecockpit_task_create/config |
| Symbol config | `app.create_symbol_config(export_comments, support_opc_ua, calculator)` | ✅ implemented | ecockpit_symbol_config_create |
| Symbol config | `sym.get_all_signatures(compile)` → signatures + variables (name/type/effective_access) | ✅ implemented | ecockpit_symbol_config_variables |
| Symbol config | `sym.get_symbol_configuration_xsd()` / `content_feature_flags` / attribute+comment filters | 🟡 possible | — |
| Device scan/comm | `online.gateways` / `gateway_drivers` | ✅ implemented | ecockpit_device_scan op=gateways |
| Device scan/comm | `gateway.perform_network_scan()` → discovered targets | ✅ implemented | ecockpit_device_scan op=scan |
| Device scan/comm | `gateway.find_address_by_ip(ip, port)` | ✅ implemented | ecockpit_device_scan op=address |
| Device scan/comm | `gateways.add_new_gateway(...)` / `remove_gateway(...)` | 🟡 possible (mutates CODESYS config) | — |
| Device scan/comm | `dev.get_device_communication_settings()` (scanned_* fields) | ✅ implemented | ecockpit_device_comm op=get |
| Device scan/comm | `dev.set_gateway_and_ip_address(gw, ip)` / `set_gateway_and_address` / `set_gateway_and_device_name` | ✅ implemented | ecockpit_device_comm op=set_ip |
| Device scan/comm | `dev.get_device_identification()` / `get_module_identification()` | 🟡 possible | — |
| Device scan/comm | `dev.get_simulation_mode()` / `set_simulation_mode(bool)` | 🟡 possible | — |
| Device tree | `project.add(name, DeviceID)` (top-level device) | 🟡 possible | — |
| Device tree | `dev.plug` / `add` / `insert(name, DeviceID, module)` / `dev.update(DeviceID)` / `dev.unplug()` | 🟡 possible (authoring + destructive unplug) | — |
| Device tree | explicit connectors: `connector.add/insert(...)` + `allowed_interfaces_at(index)` | 🟡 possible | — |
| Device params | `dev.device_parameters` / `dev.connectors.by_id(id).host_parameters` (inventory) | ✅ implemented | ecockpit_device_params op=list |
| Device params | element `.value` / `allowed_values` / `min-max` / sub-elements (offline) | ✅ implemented | ecockpit_device_params op=read |
| Device params | `el.read_online_value(timeout)` | ✅ implemented | ecockpit_device_params op=read online=true |
| Device params | `el.write_online_value(value)` (enums validated) | ✅ implemented | ecockpit_device_params op=write |
| Device params | `dev.export_io_mappings_as_csv(file)` / `import_io_mappings_from_csv(file)` | 🟡 possible (import modifies IO config) | — |
| Device params | `dev.driver_info.*` (bus cycle task, IO application, stop behaviour, diagnosis) | 🟡 possible | — |
| Device params | `mapping.variable` / `manual_iec_address` (IO mapping inspection) | 🟡 possible | — |
| Online app | `onlineapp.login(OnlineChangeOption, delete_foreign_apps)` | ✅ implemented (LIVE: Try, 15 s) | ecockpit_login + every online command |
| Online app | `OnlineChangeOption.Never/Try/Force/Keep` (Keep = stub-only, non-deploying login) | ✅ implemented (internal, G-09) | — |
| Online app | `onlineapp.logout()` | ✅ implemented | ecockpit_disconnect |
| Online app | `onlineapp.read_value(expr)` / `read_values(tuple)` | ✅ implemented | ecockpit_read |
| Online app | `onlineapp.set_prepared_value(expr, value)` + `write_prepared_values()` | ✅ implemented | ecockpit_write |
| Online app | `onlineapp.force_prepared_values()` | ✅ implemented | ecockpit_write force=true |
| Online app | `onlineapp.unforce_all_values()` | ✅ implemented | ecockpit_unforce_all |
| Online app | `get_prepared_value(expr)` / `get_prepared_expressions()` / `get_forced_expressions()` | ✅ implemented (LIVE: 0/0 idle) | ecockpit_prepared_values |
| Online app | `onlineapp.set_unforce_value(expr, restore)` | 🟡 possible | — |
| Online app | `onlineapp.start()` | ✅ implemented | ecockpit_app_control op=start |
| Online app | `onlineapp.stop()` | ✅ implemented | ecockpit_stop |
| Online app | `onlineapp.reset(ResetOption.Warm/Cold/Original, force_kill)` | ✅ implemented (DESTRUCTIVE) | ecockpit_app_control op=reset |
| Online app | `onlineapp.application_state` / `operation_state` / `is_logged_in` / `timeout` | ✅ implemented | ecockpit_state |
| Online app | `onlineapp.create_boot_application()` (online download, replaces running app) | ✅ implemented | ecockpit_download |
| Online app | `onlineapp.source_download()` | ❌ broken (live): NREs (G-04) — device path is the fix | — |
| Online app | `onlineapp.get_online_device()` | 🟡 possible | — |
| Online device (file API) | `online.create_online_device()` / `.connect()` / `.connected` / `.Dispose()` | ✅ implemented (internal; G-12 binding ladder) | — |
| Online device (file API) | `get_file_list_of_directory(dir)` | ✅ implemented (LIVE: root '.' via fallback, G-10) | ecockpit_plc_ls / ecockpit_plc_fs_ops op=ls |
| Online device (file API) | `download_file(local, remote, force)` | ✅ implemented | ecockpit_plc_get |
| Online device (file API) | `upload_file(remote, local, force)` | ✅ implemented | ecockpit_plc_put |
| Online device (file API) | `delete_file(remote)` | ✅ implemented | ecockpit_plc_rm |
| Online device (file API) | `rename_file(old, new)` / `create_directory(dir)` / `rename_directory` / `delete_directory(dir, recursive)` | ✅ implemented | ecockpit_plc_fs_ops |
| Online device (source archive) | `download_source(bCompact, additional_items)` | ✅ implemented (device path; not yet run live — app path NREs, G-04) | ecockpit_source_download |
| Online device (source archive) | `upload_source(archive_path)` | ✅ implemented (untested on the corrected object) | ecockpit_upload_source |
| Online device (admin) | `reset_origin()` | 🟡 possible (DESTRUCTIVE: factory reset) | — |
| Online device (admin) | `current_logged_on_username()` / `connected` / `shared_connected` | 🟡 possible (forced_disconnect → ecockpit_disconnect force=true) | ecockpit_disconnect |
| Online device (admin) | `create_user_management(load_from_project)` → users/groups/passwords/`download()`/`store_to_project()` | 🟡 possible (PLC-touching) | — |
| Online device (admin) | `create_live_user_management()` (SP16+) → add_user/add_group/set_user_password/backup/restore | 🟡 possible (PLC-touching) | — |
| Online device (admin) | `activate_license(ticket, url, license_names)` / `change_user_password(user, old, new)` | 🟡 possible | — |
| Online credentials | `online.set_default_credentials` / `set_specific_credentials` / `clear_all_credentials` / `auth_fallback_modes` | ✅ implemented (internal; explicit tool 🟡 possible) | — |
| Online certificates | `online.register_trusts_certificate(...)` / `unregister_*` (3.5.17.0) | 🟡 possible (encrypted SP16+ targets) | — |
| Libraries (repo) | `librarymanager.get_all_libraries(...)` / `repositories` | ✅ implemented (LIVE: oscat + System 3933) | ecockpit_libs / ecockpit_repos |
| Libraries (repo) | `find_library(display_name)` / `get_library(name, repo)` | ✅ implemented (enumeration lookups, G-13 live deviations) | ecockpit_lib_find |
| Libraries (repo) | `install_library(filepath, repo, overwrite)` | ✅ implemented | ecockpit_lib_install |
| Libraries (repo) | `uninstall_library(repo, lib)` | ✅ implemented | ecockpit_lib_uninstall |
| Libraries (repo) | `insert_repository` / `remove_repository` / `update_repository` / `move_repository` | ✅ implemented | ecockpit_repo_add/remove/update/move |
| Libraries (repo) | `get_file_path(lib)` | ✅ implemented (internal, libs listing) | — |
| Libraries (repo) | `categories` / `top_level_categories` / `get_category(guid)` | 🟡 possible (metadata only) | — |
| Libraries (in-project) | `app.get_library_manager()` → `add_library(name|ManagedLib)` / `add_placeholder(name, resolution)` / `remove_library(name)` | 🟡 possible (nearest-term; stub discovered 2026-10-22) | — |
| Libraries (in-project) | `lm.references[name]` → placeholder `.set_redirection(...)` / `.default_resolution` / `.qualified_only` / `.optional` | 🟡 possible | — |
| Libraries (in-project) | `lm.references[name].parameters["P"] = expr` (library parameters) | 🟡 possible | — |
| Device repository | `device_repository.get_all_devices(...)` / `get_device(device_id)` | 🟡 possible (installed-device inventory) | — |
| Device repository | `import_device(path, source, converter_guid, save_cache)` (EDS/GSDML/EtherCAT) | 🟡 possible | — |
| Device repository | `sources.add(name, url)` / `remove` / `move` | 🟡 possible | — |
| Device repository | `remove_device(...)` / `rebuild_device_cache()` | 🟡 possible (DESTRUCTIVE) | — |
| Device repository | `create_device_identification(...)` / `create_module_identification(...)` | 🟡 possible (needed by device-tree authoring) | — |
| Text lists | `project.create_textlist(name)` / `get_global_textlist()` → `rows.add` / `setlanguagetext` / `importfile(csv)` / `export(csv)` | 🟡 possible | — |
| Image pools | `project.create_imagepool(name)` / `get_global_imagepool()` → `images.add(id, path, linktype)` / `remove(id)` | 🟡 possible | — |
| External files | `project.create_external_file_object(path, name, reference_mode, auto_update_mode)` → `get_data(file)` / `update()` | 🟡 possible | — |
| Diagnostics | `ecockpit_probe` (legacy getattr/dir dump — G-01/G-02 limit it) | ✅ implemented | ecockpit_probe |
