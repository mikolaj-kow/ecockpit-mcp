# ScriptEngine function summary — done + possible

Master table of every ScriptEngine capability we inventoried (ScriptLib
stubs + CHM + live). **Machine-readable contract** — one row per
capability (NOT per MCP call; tools that bundle several capabilities
name them all in Tools), columns:

- **Category**: domain (the former section grouping).
- **Tool**: the MCP invocation (`—` = not exposed / internal use only).
  The human-facing per-tool reference lives in README; an
  auto-generated tool page (from the server registry) is on the
  ROADMAP.
- **Status**: ✅ implemented · 🟡 possible (stub-documented,
  unimplemented) · ❌ broken (live, engine defect) · 🗑️ removed.
- **Function / call chain**: the exact script-side invocation;
  IronPython 2.7 syntax applies.
- **Comments**: caveats, live evidence, gotcha IDs (G-nn →
  SCRIPTENGINE_GOTCHAS.md).

Details: [SCRIPTENGINE_API.md](SCRIPTENGINE_API.md) (verified),
[SCRIPTENGINE_GOTCHAS.md](SCRIPTENGINE_GOTCHAS.md),
[SCRIPTENGINE_EXPLORATION.md](SCRIPTENGINE_EXPLORATION.md) (how to add
more). Live results: [TESTING.md](TESTING.md).

| Category | Tool | Status | Function / call chain | Comments |
|---|---|---|---|---|
| System / messages | — | ✅ | `system.write_message(severity, text)` | executor logs |
| System / messages | ecockpit_messages | ✅ | `system.get_message_categories()` | consumed with get_message_objects |
| System / messages | ecockpit_messages | ✅ | `system.get_message_objects(category, severities)` | build harvest reads these |
| System / messages | ecockpit_clear_messages | ✅ | `system.clear_messages(category)` | iterates all categories |
| System / messages | — | ✅ | `system.delay(ms)` / `process_messageloop()` | internal |
| System / messages | ecockpit_system / ecockpit_system_ui | ✅ | `system.prompt_handling` get/set / `prompt_answers` dict (headless dialog simulation) | answers persist per session; prompt_handling re-hardened per command (relay re-exec); ForwardSimplePrompts confirm-gated |
| System / messages | — | ❌ | `system.get_message_category_description(category)` | not callable in this build |
| System / messages | — | ❌ | `system.get_message_log` / `get_messages_count` | absent |
| System / messages | ecockpit_system | ✅ | `system.progress_start/step/absolute`, `abortable`, `aborting`, `abort_autocheck`, `trace`, `commands`, `factories` | progress = GUI chrome; flags read/set; info op |
| System / messages | ecockpit_system_ui | ✅ | `system.ui.error/warning/info/prompt/select_many/choose/query_string/query_password/query_credentials/file dialogs` | CONFIRM-GATED: real modal dialogs, wedge hazard (G-08); prompt_answers does NOT cover them (stub) |
| System / messages | ecockpit_system_ui (op=exit) | ✅ | `system.exit(code)` | DESTRUCTIVE: kills e!COCKPIT + relay; confirm-gated |
| Project lifecycle | ecockpit_status | ✅ | `projects.primary` / `get_by_path(path)` / `all` | internal (`get_project`) |
| Project lifecycle | ecockpit_project_control | ✅ | `projects.create(path, primary)` | new project becomes primary |
| Project lifecycle | ecockpit_project_control | ✅ | `projects.open(path, password, primary, update_flags)` | becomes primary |
| Project lifecycle | ecockpit_project_control | ✅ | `projects.open_archive(archive, projectpath, overwrite)` | restores an archive |
| Project lifecycle | — | 🟡 | `projects.convert(path, output_path, converter, primary)` | V2.3 `.pro`/`.lib` converter GUIDs in stub |
| Project lifecycle | ecockpit_project_control | ✅ | `project.save()` | physical location |
| Project lifecycle | — | 🟡 | `project.save_as(path, password)` | retargets the open project |
| Project lifecycle | — | ❌ 🗑️ | `project.save_archive(path, comment, additional_files, *cats)` | NREs in all 6 bindings, saved or not (G-14); tool removed |
| Project lifecycle | ecockpit_project_control | ✅ | `project.close()` | DISCARDS unsaved changes |
| Project lifecycle | ecockpit_project_control | ✅ | `project.dirty` / `project.path` | info op |
| Project lifecycle | ecockpit_project_control | ✅ | `project.get_project_info()` → company/title/version/author/description | info/info_set |
| Project lifecycle | — | 🟡 | `project.login(username, password)` / `logout()` (project user mgmt) | sensitive: session users |
| Project lifecycle | — | 🟡 | `project.user_management` (users/groups/permissions) | sensitive: ACLs |
| Project lifecycle | — | 🟡 | `project.check_all_pool_objects()` / `save_as_compiled_library(dst)` / `generate_runtime_system_files(...)` | library-project features |
| Project lifecycle | — | 🟡 | `project.compare_to(right, flags)` → differences | 3.5.12.0 |
| Project lifecycle | — | 🟡 | `project.project_settings.set_sourcedownload(timing, content, compact, device)` / `available_download_content` | 3.5.12.10; configures `download_source` packing |
| Project lifecycle | — | 🟡 | `project.enable_integrity_check()` / `disable_encryption()` | security-setting change |
| Tree / objects | ecockpit_tree | ✅ | `project.find(name, recursive)` / `get_children(recursive)` | op=list (read-only) |
| Tree / objects | ecockpit_tree | ✅ | `obj.get_name()` / `guid` / kind markers | op=list |
| Tree / objects | ecockpit_tree | ✅ | `obj.rename(new_name)` | op=rename (confirm) |
| Tree / objects | ecockpit_tree | ✅ | `obj.remove()` | op=remove (confirm; DELETES) |
| Tree / objects | ecockpit_tree | ✅ | `obj.move(parent, index)` | op=move (confirm) |
| Tree / objects | ecockpit_tree | ✅ | `obj.create_folder(name)` / `project.create_folder(name, view)` | op=folder (confirm) |
| Tree / objects | — | 🟡 | `obj.get_signature_crc()` | needs a successful build |
| Tree / objects | — | 🟡 | `obj.build_properties.*` (exclude_from_build, ...) | per-object build exclusion |
| Tree / objects | — | 🟡 | `obj.export_native` / `import_native` | native (non-PLCopen) format |
| ST authoring | ecockpit_object_create | ✅ | `container.create_pou(name, type=PouType.*, language=ImplementationLanguages.st, return_type)` | kind=pou |
| ST authoring | ecockpit_object_create | ✅ | `container.create_gvl(name)` | kind=gvl |
| ST authoring | ecockpit_object_create | ✅ | `container.create_dut(name, type=DutType.*, baseType)` | kind=dut |
| ST authoring | ecockpit_object_create | ✅ | `container.create_interface(name, baseInterfaces)` | kind=interface |
| ST authoring | ecockpit_object_create | ✅ | `pou.create_method/property/action/transition(...)` | kind=method/property/action/transition (needs parent) |
| ST authoring | ecockpit_textual_read/write | ✅ | `obj.textual_declaration` / `textual_implementation` → `.text` / `.replace(new_text=...)` | read / confirm-gated whole-part replace |
| ST authoring | — | 🟡 | `create_pou(SpecialPouType.*)` (check functions) | forces full recompile, kills online change |
| Task config | ecockpit_task_config | ✅ | `app.create_task_configuration()` → `tc.create_task(name)` | inspect (read-only) + create |
| Task config | ecockpit_task_create | ✅ | `task.kind_of_task` (KindOfTask.Cyclic/...) / `priority` / `interval` / `interval_unit` | task_create sets, task_config inspects |
| Task config | ecockpit_task_create | ✅ | `task.watchdog.{enabled,time,time_unit,sensitivity}` | — |
| Task config | ecockpit_task_create | ✅ | `task.pous.add/insert/replace/remove` | — |
| Symbol config | ecockpit_symbol_config_create | ✅ | `app.create_symbol_config(export_comments, support_opc_ua, calculator)` | confirm-gated |
| Symbol config | ecockpit_symbol_config_variables | ✅ | `sym.get_all_signatures(compile)` → signatures + variables (name/type/effective_access) | compile=true builds first (slow, safe) |
| Symbol config | — | 🟡 | `sym.get_symbol_configuration_xsd()` / `content_feature_flags` / attribute+comment filters | — |
| Device scan/comm | ecockpit_device_scan | ✅ | `online.gateways` / `gateway_drivers` | op=gateways (LIVE: WACDefaultGateway) |
| Device scan/comm | ecockpit_device_scan | ✅ | `gateway.perform_network_scan()` → discovered targets | op=scan (blocks for scan timeout) |
| Device scan/comm | ecockpit_device_scan | ✅ | `gateway.find_address_by_ip(ip, port)` | op=address |
| Device scan/comm | — | 🟡 | `gateways.add_new_gateway(...)` / `remove_gateway(...)` | mutates CODESYS config |
| Device scan/comm | ecockpit_device_comm | ✅ | `dev.get_device_communication_settings()` (scanned_* fields) | op=get |
| Device scan/comm | ecockpit_device_comm | ✅ | `dev.set_gateway_and_ip_address(gw, ip)` / `set_gateway_and_address` / `set_gateway_and_device_name` | op=set_ip (confirm) |
| Device scan/comm | ecockpit_device_info | ✅ | `dev.get_device_identification()` / `get_module_identification()` | op=identification (read-only); DeviceId = {type, id, version} |
| Device scan/comm | ecockpit_device_info | ✅ | `dev.get_simulation_mode()` / `set_simulation_mode(bool)` | op=simulation (read) / simulation_set (confirm) |
| Device tree | — | 🟡 | `project.add(name, DeviceID)` (top-level device) | DeviceID via injected `DeviceID(type, id, version)` |
| Device tree | — | 🟡 | `dev.plug` / `add` / `insert(name, DeviceID, module)` / `dev.update(DeviceID)` / `dev.unplug()` | unplug destructive |
| Device tree | — | 🟡 | explicit connectors: `connector.add/insert(...)` + `allowed_interfaces_at(index)` | — |
| Device params | ecockpit_device_params | ✅ | `dev.device_parameters` / `dev.connectors.by_id(id).host_parameters` | op=list (inventory) |
| Device params | ecockpit_device_params | ✅ | element `.value` / `allowed_values` / `min-max` / sub-elements | op=read (offline) |
| Device params | ecockpit_device_params | ✅ | `el.read_online_value(timeout)` | op=read online=true |
| Device params | ecockpit_device_params | ✅ | `el.write_online_value(value)` (enums validated) | op=write (confirm; PLC-touching) |
| Device params | ecockpit_device_io | ✅ | `dev.export_io_mappings_as_csv(file)` / `import_io_mappings_from_csv(file)` | op=export_csv (default `<ECP_PROJECT_ROOT>/io-mappings/`) / import_csv (confirm; modifies IO config) |
| Device params | ecockpit_device_info | ✅ | `dev.driver_info.*` (bus cycle task, IO application, stop behaviour, diagnosis) | op=driver_info (read-only) / driver_info_set (confirm; set_bus_cycle_task / set_io_application + bool/enum setters) |
| Device params | ecockpit_device_io | ✅ | `ScriptDataElement.is_mappable_io` → `io_mapping` (`mapping.variable` / `manual_iec_address` / `automatic_iec_address` / ...) | op=mappings (inspection, read-only) / set_mapping (confirm) |
| Online app | ecockpit_login | ✅ | `onlineapp.login(OnlineChangeOption, delete_foreign_apps)` | LIVE: Try, 15 s; Keep = non-deploying login (G-09) |
| Online app | (internal, G-09) | ✅ | `OnlineChangeOption.Never/Try/Force/Keep` | Keep is stub-only; every online command uses the ladder |
| Online app | ecockpit_disconnect | ✅ | `onlineapp.logout()` | force=true → forced_disconnect |
| Online app | ecockpit_read | ✅ | `onlineapp.read_value(expr)` / `read_values(tuple)` | — |
| Online app | ecockpit_write | ✅ | `onlineapp.set_prepared_value(expr, value)` + `write_prepared_values()` | reads back after write |
| Online app | ecockpit_write | ✅ | `onlineapp.force_prepared_values()` | force=true |
| Online app | ecockpit_unforce_all | ✅ | `onlineapp.unforce_all_values()` | — |
| Online app | ecockpit_prepared_values | ✅ | `get_prepared_value(expr)` / `get_prepared_expressions()` / `get_forced_expressions()` | LIVE: 0/0 idle |
| Online app | — | 🟡 | `onlineapp.set_unforce_value(expr, restore)` | — |
| Online app | ecockpit_app_control | ✅ | `onlineapp.start()` | confirm-gated |
| Online app | ecockpit_stop | ✅ | `onlineapp.stop()` | confirm-gated |
| Online app | ecockpit_app_control | ✅ | `onlineapp.reset(ResetOption.Warm/Cold/Original, force_kill)` | DESTRUCTIVE; original erases everything |
| Online app | ecockpit_state | ✅ | `onlineapp.application_state` / `operation_state` / `is_logged_in` / `timeout` | — |
| Online app | ecockpit_download | ✅ | `onlineapp.create_boot_application()` (online download, replaces running app) | confirm-gated |
| Online app | — | ❌ | `onlineapp.source_download()` | NREs (G-04); device path is the fix |
| Online app | — | 🟡 | `onlineapp.get_online_device()` | — |
| Online device (file API) | (internal) | ✅ | `online.create_online_device()` / `.connect()` / `.connected` / `.Dispose()` | binding ladder (G-12) |
| Online device (file API) | ecockpit_plc_ls / plc_fs_ops op=ls | ✅ | `get_file_list_of_directory(dir)` | LIVE: root '.' via fallback (G-10) |
| Online device (file API) | ecockpit_plc_get | ✅ | `download_file(local, remote, force)` | — |
| Online device (file API) | ecockpit_plc_put | ✅ | `upload_file(remote, local, force)` | confirm-gated |
| Online device (file API) | ecockpit_plc_rm | ✅ | `delete_file(remote)` | confirm-gated |
| Online device (file API) | ecockpit_plc_fs_ops | ✅ | `rename_file(old, new)` / `create_directory(dir)` / `rename_directory` / `delete_directory(dir, recursive)` | confirm-gated |
| Online device (source archive) | ecockpit_source_download | ✅ | `odev.download_source(bCompact, additional_items)` | implemented, not yet run live; app-path NREs (G-04) |
| Online device (source archive) | ecockpit_upload_source | ✅ | `odev.upload_source(archive_path)` | untested on the corrected object |
| Online device (admin) | — | 🟡 | `odev.reset_origin()` | DESTRUCTIVE: factory reset |
| Online device (admin) | ecockpit_disconnect | 🟡 | `odev.current_logged_on_username()` / `connected` / `shared_connected` | forced_disconnect → force=true |
| Online device (admin) | — | 🟡 | `odev.create_user_management(load_from_project)` → users/groups/passwords/`download()`/`store_to_project()` | PLC-touching |
| Online device (admin) | — | 🟡 | `odev.create_live_user_management()` (SP16+) → add_user/add_group/set_user_password/backup/restore | PLC-touching |
| Online device (admin) | — | 🟡 | `odev.activate_license(ticket, url, license_names)` / `change_user_password(user, old, new)` | — |
| Credentials | (internal) | ✅ | `online.set_default_credentials` / `set_specific_credentials` / `clear_all_credentials` / `auth_fallback_modes` | login ladder uses defaults; explicit tool 🟡 possible |
| Certificates | — | 🟡 | `online.register_trusts_certificate(...)` / `unregister_*` (3.5.17.0) | encrypted SP16+ targets |
| Libraries (repo) | ecockpit_libs / ecockpit_repos | ✅ | `librarymanager.get_all_libraries(...)` / `repositories` | LIVE: oscat + System 3933 |
| Libraries (repo) | ecockpit_lib_find | ✅ | `find_library(display_name)` / `get_library(name, repo)` | enumeration lookups (live deviations) |
| Libraries (repo) | ecockpit_lib_install | ✅ | `install_library(filepath, repo, overwrite)` | — |
| Libraries (repo) | ecockpit_lib_uninstall | ✅ | `uninstall_library(repo, lib)` | — |
| Libraries (repo) | ecockpit_repo_add/remove/update/move | ✅ | `insert_repository` / `remove_repository` / `update_repository` / `move_repository` | — |
| Libraries (repo) | (internal) | ✅ | `get_file_path(lib)` | libs listing |
| Libraries (repo) | — | 🟡 | `categories` / `top_level_categories` / `get_category(guid)` | metadata only |
| Libraries (in-project) | — | 🟡 | `app.get_library_manager()` → `add_library(name\|ManagedLib)` / `add_placeholder(name, resolution)` / `remove_library(name)` | nearest-term; stub discovered 2026-10-22 |
| Libraries (in-project) | — | 🟡 | `lm.references[name]` → placeholder `.set_redirection(...)` / `.default_resolution` / `.qualified_only` / `.optional` | — |
| Libraries (in-project) | — | 🟡 | `lm.references[name].parameters["P"] = expr` (library parameters) | — |
| Device repository | — | 🟡 | `device_repository.get_all_devices(...)` / `get_device(device_id)` | installed-device inventory |
| Device repository | — | 🟡 | `import_device(path, source, converter_guid, save_cache)` (EDS/GSDML/EtherCAT) | — |
| Device repository | — | 🟡 | `sources.add(name, url)` / `remove` / `move` | — |
| Device repository | — | 🟡 | `remove_device(...)` / `rebuild_device_cache()` | DESTRUCTIVE |
| Device repository | — | 🟡 | `create_device_identification(...)` / `create_module_identification(...)` | needed by device-tree authoring |
| Text lists | — | 🟡 | `project.create_textlist(name)` / `get_global_textlist()` → `rows.add` / `setlanguagetext` / `importfile(csv)` / `export(csv)` | — |
| Image pools | — | 🟡 | `project.create_imagepool(name)` / `get_global_imagepool()` → `images.add(id, path, linktype)` / `remove(id)` | — |
| External files | — | 🟡 | `project.create_external_file_object(path, name, reference_mode, auto_update_mode)` → `get_data(file)` / `update()` | — |
| Diagnostics | ecockpit_probe | ✅ | legacy getattr/dir dump | limited by G-01/G-02 |
