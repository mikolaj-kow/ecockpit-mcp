# ScriptEngine function summary — done + possible

Master table of every ScriptEngine capability we inventoried (ScriptLib
stubs + CHM + live), aggregated per domain. **Machine-readable
contract**: Status ∈ `tool: <name>` (exposed, live-verified where
noted) · `possible` (stub-documented, unimplemented — see
SCRIPTENGINE_NOT_EXPOSED.md) · `broken (live)` (engine defect — see
SCRIPTENGINE_GOTCHAS.md) · `removed`. Call chains are the exact
script-side invocations; IronPython 2.7 syntax applies.

Legend for the Tools column: the MCP tool name, `—` = no tool.

## System / messages

| Function / call chain | Status | Tools |
|---|---|---|
| `system.write_message(severity, text)` | tool: ecockpit_* logs | — |
| `system.get_message_categories()` | tool: ecockpit_messages / clear_messages | — |
| `system.get_message_objects(category, severities)` | tool: ecockpit_messages + build harvest | — |
| `system.clear_messages(category)` | tool: ecockpit_clear_messages | — |
| `system.get_message_category_description(category)` | broken (live): not callable in this build | — |
| `system.get_message_log` / `get_messages_count` | broken (live): absent | — |
| `system.get_message_category_description` | possible | — |
| `system.delay(ms)`, `system.process_messageloop()` | used internally by executor | — |
| `system.prompt_handling` / `prompt_answers` (headless dialog simulation) | possible (headless hardening covers simple prompts only) | — |
| `system.progress_start/step/absolute`, `system.abortable`, `system.exit(code)` | possible (exit is DESTRUCTIVE: kills e!COCKPIT) | — |
| `system.ui.*` (dialogs) | do not use unattended (G-08) | — |

## Project lifecycle / info

| Function / call chain | Status | Tools |
|---|---|---|
| `projects.primary` / `get_by_path(path)` / `all` | internal (executor `get_project`) | ecockpit_status |
| `projects.create(path, primary)` | tool: ecockpit_project_control op=create | ecockpit_project_control |
| `projects.open(path, password, primary, update_flags)` | tool: ecockpit_project_control op=open | ecockpit_project_control |
| `projects.open_archive(archive, projectpath, overwrite)` | tool: ecockpit_project_control op=open_archive | ecockpit_project_control |
| `projects.convert(path, output_path, converter, primary)` | possible | — |
| `project.save()` | tool: ecockpit_project_control op=save (also inside snapshot/source_download) | ecockpit_project_control |
| `project.save_as(path, password)` | possible (retargets the open project) | — |
| `project.save_archive(path, comment, additional_files, *cats)` | broken (live): NREs in all 6 bindings — ladder kept | — |
| `project.close()` | tool: ecockpit_project_control op=close (DISCARDS unsaved changes) | ecockpit_project_control |
| `project.dirty` / `project.path` | internal + info op | ecockpit_project_control |
| `project.get_project_info()` → company/title/version/author/... | tool: ecockpit_project_control info/info_set | ecockpit_project_control |
| `project.login/logout` (project user mgmt) | possible | — |
| `project.user_management` (users/groups/permissions) | possible | — |
| `project.check_all_pool_objects()` / `save_as_compiled_library(dst)` / `generate_runtime_system_files(...)` | possible | — |
| `project.compare_to(right, flags)` → differences | possible | — |
| `project.project_settings.set_sourcedownload(timing, content, compact, device)` | possible | — |
| `project.enable_integrity_check()` / `disable_encryption()` | possible (destructive to security settings) | — |

## Tree / objects

| Function / call chain | Status | Tools |
|---|---|---|
| `project.find(name, recursive)` / `get_children(recursive)` | tool: ecockpit_tree op=list | ecockpit_tree |
| `obj.get_name()` / `guid` / kind markers | tool: ecockpit_tree op=list | ecockpit_tree |
| `obj.rename(new_name)` | tool: ecockpit_tree op=rename | ecockpit_tree |
| `obj.remove()` | tool: ecockpit_tree op=remove | ecockpit_tree |
| `obj.move(parent, index)` | tool: ecockpit_tree op=move | ecockpit_tree |
| `obj.create_folder(name)` / `project.create_folder(name, view)` | tool: ecockpit_tree op=folder | ecockpit_tree |
| `obj.get_signature_crc()` | possible | — |
| `obj.build_properties.*` (exclude_from_build, ...) | possible | — |
| `obj.export_native` / `import_native` | possible | — |

## ST authoring (POU / GVL / DUT)

| Function / call chain | Status | Tools |
|---|---|---|
| `container.create_pou(name, type=PouType.*, language=ImplementationLanguages.st, return_type)` | tool: ecockpit_object_create kind=pou | ecockpit_object_create |
| `container.create_gvl(name)` | tool: ecockpit_object_create kind=gvl | ecockpit_object_create |
| `container.create_dut(name, type=DutType.*, baseType)` | tool: ecockpit_object_create kind=dut | ecockpit_object_create |
| `container.create_interface(name, baseInterfaces)` | tool: ecockpit_object_create kind=interface | ecockpit_object_create |
| `pou.create_method/property/action/transition(...)` | tool: ecockpit_object_create kind=method/property/action/transition | ecockpit_object_create |
| `obj.textual_declaration` / `textual_implementation` → `.text` / `.replace(new_text=...)` | tool: ecockpit_textual_read / ecockpit_textual_write | ecockpit_textual_read/write |
| `create_pou(SpecialPouType.*)` (check functions) | possible (forces full recompile, kills online change) | — |

## Task configuration

| Function / call chain | Status | Tools |
|---|---|---|
| `app.create_task_configuration()` → `tc.create_task(name)` | tool: ecockpit_task_create (inspect: ecockpit_task_config) | ecockpit_task_create/config |
| `task.kind_of_task / priority / interval / interval_unit / event / core_binding / parent_synchron_task` | tool: ecockpit_task_config op=inspect (set: task_create) | ecockpit_task_config/create |
| `task.watchdog.{enabled,time,time_unit,sensitivity}` | tool: ecockpit_task_create (+inspect) | ecockpit_task_create/config |
| `task.pous.add/insert/replace/remove` | tool: ecockpit_task_create (set: task_create) | ecockpit_task_create/config |

## Symbol configuration

| Function / call chain | Status | Tools |
|---|---|---|
| `app.create_symbol_config(export_comments, support_opc_ua, calculator)` | tool: ecockpit_symbol_config_create | ecockpit_symbol_config_create |
| `sym.get_all_signatures(compile)` → variables (name/type/effective_access) | tool: ecockpit_symbol_config_variables | ecockpit_symbol_config_variables |
| `sym.get_symbol_configuration_xsd()` / content_feature_flags / attribute filters | possible | — |

## Device tree / scan / communication

| Function / call chain | Status | Tools |
|---|---|---|
| `online.gateways` / `gateway_drivers` | tool: ecockpit_device_scan op=gateways | ecockpit_device_scan |
| `gateway.perform_network_scan()` | tool: ecockpit_device_scan op=scan | ecockpit_device_scan |
| `gateway.find_address_by_ip(ip, port)` | tool: ecockpit_device_scan op=address | ecockpit_device_scan |
| `gateways.add_new_gateway(...)` / `remove_gateway(...)` | possible (mutates CODESYS config) | — |
| `dev.get_device_communication_settings()` | tool: ecockpit_device_comm op=get | ecockpit_device_comm |
| `dev.set_gateway_and_ip_address(gw, ip)` / `set_gateway_and_address` / `set_gateway_and_device_name` | tool: ecockpit_device_comm op=set_ip | ecockpit_device_comm |
| `dev.get_device_identification()` / `get_module_identification()` | possible | — |
| `project.add(name, DeviceID)` / `dev.plug/add/insert(...)` / `dev.update(DeviceID)` / `dev.unplug()` | possible (device tree authoring) | — |
| `dev.get_simulation_mode()` / `set_simulation_mode(bool)` | possible | — |
| `dev.allow_interfaces_at(index)` / explicit connectors `connector.add/insert` | possible | — |

## Device parameters

| Function / call chain | Status | Tools |
|---|---|---|
| `dev.device_parameters` / `dev.connectors.by_id(id).host_parameters` | tool: ecockpit_device_params op=list | ecockpit_device_params |
| `param[...]` elements: value / allowed_values / min-max / sub-elements | tool: ecockpit_device_params op=read (offline) | ecockpit_device_params |
| `el.read_online_value(timeout)` | tool: ecockpit_device_params op=read online=true | ecockpit_device_params |
| `el.write_online_value(value)` | tool: ecockpit_device_params op=write (confirm-gated) | ecockpit_device_params |
| `dev.export_io_mappings_as_csv(file)` / `import_io_mappings_from_csv(file)` | possible | — |
| `dev.driver_info.*` (bus cycle task, IO application, stop behaviour, diagnosis) | possible | — |
| `mapping.variable / manual_iec_address` (IO mapping inspection) | possible | — |

## Online application (login / prepared values / control)

| Function / call chain | Status | Tools |
|---|---|---|
| `onlineapp.login(OnlineChangeOption, delete_foreign_apps)` | tool: ecockpit_login + login ladder in every online command (LIVE: Try, 15 s) | ecockpit_login |
| `OnlineChangeOption.Never/Try/Force/Keep` | internal (Keep is stub-only) | — |
| `onlineapp.logout()` | via ecockpit_disconnect | ecockpit_disconnect |
| `onlineapp.read_value(expr)` / `read_values(tuple)` | tool: ecockpit_read | ecockpit_read |
| `onlineapp.set_prepared_value(expr, value)` + `write_prepared_values()` | tool: ecockpit_write | ecockpit_write |
| `onlineapp.force_prepared_values()` | tool: ecockpit_write force=true | ecockpit_write |
| `onlineapp.unforce_all_values()` | tool: ecockpit_unforce_all | ecockpit_unforce_all |
| `onlineapp.get_prepared_value(expr)` / `get_prepared_expressions()` / `get_forced_expressions()` | tool: ecockpit_prepared_values (LIVE: 0/0 idle) | ecockpit_prepared_values |
| `onlineapp.set_unforce_value(expr, restore)` | possible | — |
| `onlineapp.start()` / `stop()` | tool: ecockpit_app_control op=start (stop: ecockpit_stop) | ecockpit_app_control/stop |
| `onlineapp.reset(ResetOption.Warm/Cold/Original, force_kill)` | tool: ecockpit_app_control op=reset (DESTRUCTIVE) | ecockpit_app_control |
| `onlineapp.application_state` / `operation_state` / `is_logged_in` / `timeout` | tool: ecockpit_state | ecockpit_state |
| `onlineapp.create_boot_application()` (online download) | tool: ecockpit_download (confirm-gated) | ecockpit_download |
| `onlineapp.source_download()` | broken (live): NREs — see device.download_source | — |
| `onlineapp.get_online_device()` | possible | — |

## Online device (file API / source archive / admin)

| Function / call chain | Status | Tools |
|---|---|---|
| `online.create_online_device()` / `.connect()` / `.connected` / `.Dispose()` | internal (plcfs/upload_source/source_download) | — |
| `odev.get_file_list_of_directory(dir)` | tool: ecockpit_plc_ls (LIVE: root via '.') | ecockpit_plc_ls |
| `odev.download_file(local, remote, force)` | tool: ecockpit_plc_get | ecockpit_plc_get |
| `odev.upload_file(remote, local, force)` | tool: ecockpit_plc_put | ecockpit_plc_put |
| `odev.delete_file(remote)` | tool: ecockpit_plc_rm | ecockpit_plc_rm |
| `odev.rename_file(old, new)` / `create_directory(dir)` / `rename_directory` / `delete_directory(dir, recursive)` | tool: ecockpit_plc_fs_ops | ecockpit_plc_fs_ops |
| `odev.download_source(bCompact, additional_items)` | tool: ecockpit_source_download (untested live — application fallback NREs, device path per stubs) | ecockpit_source_download |
| `odev.upload_source(archive_path)` | tool: ecockpit_upload_source (re-added; untested on the corrected object) | ecockpit_upload_source |
| `odev.reset_origin()` | possible (DESTRUCTIVE: factory reset) | — |
| `odev.current_logged_on_username()` / `connected` / `shared_connected` / `forced_disconnect` | possible (forced_disconnect exposed via ecockpit_disconnect force=true) | ecockpit_disconnect |
| `odev.create_user_management(load_from_project)` → users/groups/passwords/`download()`/`store_to_project()` | possible (PLC-touching) | — |
| `odev.create_live_user_management()` (SP16+) → add_user/add_group/set_user_password/backup/restore | possible (PLC-touching) | — |
| `odev.activate_license(ticket, url, license_names)` / `change_user_password(user, old, new)` | possible | — |
| `online.set_default_credentials` / `set_specific_credentials` / `clear_all_credentials` / `auth_fallback_modes` | internal (login ladder) + possible (explicit headless hardening tool) | — |
| `online.register_trusts_certificate(...)` / `unregister_*` (3.5.17.0) | possible (encrypted SP16+ targets) | — |

## Libraries (repository + in-project)

| Function / call chain | Status | Tools |
|---|---|---|
| `librarymanager.repositories` / `get_all_libraries(...)` | tool: ecockpit_libs / ecockpit_repos | ecockpit_libs/repos |
| `librarymanager.find_library(display_name)` / `get_library(name, repo)` | tool: ecockpit_lib_find (enumeration-based — see GOTCHAS) | ecockpit_lib_find |
| `librarymanager.install_library(filepath, repo, overwrite)` | tool: ecockpit_lib_install | ecockpit_lib_install |
| `librarymanager.uninstall_library(repo, lib)` | tool: ecockpit_lib_uninstall | ecockpit_lib_uninstall |
| `librarymanager.insert_repository` / `remove_repository` / `update_repository` / `move_repository` | tools: ecockpit_repo_add/remove/update/move | ecockpit_repo_* |
| `librarymanager.get_file_path(lib)` | internal (libs listing) | — |
| `librarymanager.categories` / `top_level_categories` / `get_category(guid)` | possible (metadata only) | — |
| `app.get_library_manager()` → `add_library(name|ManagedLib)` / `add_placeholder(name, resolution)` / `remove_library(name)` | possible (in-project LM — ScriptLibManObject stub; nearest-term) | — |
| `lm.references[name]` → placeholder `.set_redirection(...)` / `.default_resolution` / `.qualified_only` / `.optional` | possible | — |
| `lm.references[name].parameters["P"] = expr` (library parameters) | possible | — |

## Device repository (descriptions)

| Function / call chain | Status | Tools |
|---|---|---|
| `device_repository.get_all_devices(...)` / `get_device(device_id)` | possible (installed-device inventory) | — |
| `device_repository.import_device(path, source, converter_guid, save_cache)` (EDS/GSDML/EtherCAT converters) | possible | — |
| `device_repository.sources.add(name, url)` / `remove` / `move` | possible | — |
| `device_repository.remove_device(...)` / `rebuild_device_cache()` | possible (DESTRUCTIVE) | — |
| `device_repository.create_device_identification(...)` / `create_module_identification(...)` | possible (needed by device-tree authoring) | — |

## External files / text lists / image pools

| Function / call chain | Status | Tools |
|---|---|---|
| `project.create_external_file_object(path, name, reference_mode, auto_update_mode)` → `get_data(file)` / `update()` | possible | — |
| `project.create_textlist(name)` / `get_global_textlist()` → `rows.add` / `setlanguagetext` / `importfile(csv)` / `export(csv)` | possible | — |
| `project.create_imagepool(name)` / `get_global_imagepool()` → `images.add(id, path, linktype)` | possible | — |

## Project archive / backup

| Function / call chain | Status | Tools |
|---|---|---|
| `project.save()` + file copy (snapshot) | tool: ecockpit_snapshot | ecockpit_snapshot |
| `online.create_online_device(dev).download_source(bCompact, additional_items)` | tool: ecockpit_source_download (untested on the device path) | ecockpit_source_download |
| `online.create_online_device(dev).upload_source(archive_path)` | tool: ecockpit_upload_source (untested on the corrected object) | ecockpit_upload_source |
| `project.save_archive(...)` | removed (broken live — GOTCHAS G-14) | — |
| `projects.open_archive(archive, projectpath, overwrite)` | tool: ecockpit_project_control op=open_archive | ecockpit_project_control |
