# e!COCKPIT ScriptEngine API — verified reference

Distilled from the official help file shipped with this install. All
install-relative paths in the SCRIPTENGINE_* docs use **`ECP_INSTALL_DIR`**
= your e!COCKPIT install dir (this machine:
`D:\Program Files (x86)\WAGO Software\e!COCKPIT`).

```
ECP_INSTALL_DIR\3S CODESYS\CODESYS\Online Help\en\ScriptEngine.chm
```

Decompile with: `7z x ScriptEngine.chm -o%TEMP%\scriptengine_chm`
(~2200 HTML topics; namespace `_3S.CoDeSys.ScriptEngine.BasicFunctionality`,
ScriptEngine.dll 3.5.11.1, IronPython 2.7 runtime). Everything below was
additionally verified LIVE in e!COCKPIT 11.3.1.0 on 2026-10-20.

## Gotchas

All ScriptEngine gotchas live in [SCRIPTENGINE_GOTCHAS.md](SCRIPTENGINE_GOTCHAS.md)
(stable IDs `G-nn`, machine-readable table). Umbrella rules:

- **G-01/G-02** — `getattr()` fails and `dir()` is empty on the interop
  wrappers: direct attribute access inside try/except only; discovery
  via the ScriptLib stubs or CLR reflection.
- **G-03/G-04/G-12** — watch the object you call: the file/source API
  lives on the `ScriptOnlineDevice` from `online.create_online_device()`,
  not on the project-tree device; `onlineapp.source_download()` NREs
  while `device.download_source()` is the stub-documented form.
- **G-05/G-16** — reporter classes subclass the *injected* name
  (`ExportReporter`) and take `*argv` callbacks.
- **G-07/G-14** — engine-NRE class (`save_archive`, …): record the
  verdict in TESTING.md, keep self-healing ladders, name the working
  alternative.
- **G-09/G-10/G-11/G-17** — deployment and path semantics: implicit
  login-with-download, PLC root `.`, `source_download` needs a saved
  project, `export_xml` takes a FILE path.

## IScriptApplication (project.find("Application", True)[0])

| Member | Notes |
|---|---|
| `build()` | compile; returns cleanly even on compile errors; emits full messages to the store |
| `rebuild()` | build after clean |
| `clean()` | removes intermediate state (fixes C0106-class stale-incremental) |
| `generate_code()` | code generation stage; same message-check pattern |
| `create_boot_application(path)` | offline boot project (also compiles; its failure mode on errors is a content-free NullReferenceException at the final step) |
| `is_active_application` | property |

The build/generate_code remarks: *"You can use the get_messages(Guid) and
get_message_objects(Guid, Severity) calls to check whether any messages
were added."* — that is the sanctioned diagnostics loop. Only works for
applications in the primary project.

## Messages (ISystem3 / IScriptMessage)

- `system.get_message_categories()` → list of category GUIDs. Unstable
  across restarts; many registered, few hold content. Categories are
  source-separated (build/compile vs script vs tracer).
- `system.get_message_objects(category, severities)` →
  `IList<IScriptMessage>`; `category` defaults to the **ScriptMessage**
  category (why the no-arg form returns only script messages). The
  single-arg form works (severity defaults to all).
- `system.get_messages(category)` → plain texts. **Always returned 0
  lines in practice** — use the objects.
- `system.get_message_category_description(...)` — not callable in this
  build.
- `system.write_message(Severity, text)` — write-only; our own texts land
  in the store and must be filtered out when harvesting.
- `system.process_messageloop()` — UI-loop pump; not needed for message
  visibility.
- `system.clear_messages(category_guid)` — clears ONE category (probed
  live: the 0-arg call raises TypeError; iterate `get_message_categories()`
  to wipe the store). Exposed as the MCP `ecockpit_clear_messages` tool —
  call before a build to guarantee a fresh harvest.
- There is **no** `get_message_log` / `get_messages_count` (web articles
  claiming so are wrong for this build).

**IScriptMessage members** (verified):

| Member | Meaning |
|---|---|
| `prefix` | source system id: `"compile"`, `"LINT"`, `"IO-Config"`, …; script/system messages have none (falsy) |
| `number` | the code number → with prefix the C-code (e.g. prefix `C` + number `40` = `C40`) |
| `text` | message body — WITHOUT the C-code prefix |
| `severity` | enum; str() gives `Text` / `Information` / `Warning` / `Error` / `FatalError` |
| `object` | the POU/database object of the message position |
| `position` / `position_text` / `position_offset`, `length` | position within the object; `position_text` is the user-friendly form (`Line 4, Column 1 (Impl)`) |
| `icon`, `FontColor`, `has_details_handler`, `call_details_handler`, `project` | UI details |

Build log rendering used by the executor:
`C40: Error: Function 'mBuildState' requires exactly '3' inputs [Name=MqttPubLight Line 4, Column 1 (Impl)]`

## System object (ScriptSystem stub — the injected `system` global)

Implemented on feat/system-device-io (→ `ecockpit_system`,
`ecockpit_system_ui`); live status per TESTING.md. Signatures from
`ScriptLib\Stubs\scriptengine\ScriptSystem.pyi`.

| Member | Notes |
|---|---|
| `ui_present`, `executable_filename` | environment facts (read) |
| `trace` (get/set) | script-trace messages into the store |
| `prompt_handling` (get/set) | PromptHandling flags: ForwardSimplePrompts=1, LogSimplePrompts=2, EnableTextPrompts=4, ProcessScriptPrompts=8, LogMessageKeys=16 (+NONE=0). The executor re-hardens LogSimplePrompts at EVERY command start (the relay re-execs executor.py), so a set lasts only for the current command |
| `prompt_answers` (dict) | headless dialog simulation: key = prompt message key, value = PromptResult member (OK=0 Cancel=1 Yes=2 No=3 Retry=4 Abort=5 Ignore=6). Persists for the engine session. `system.ui.*` dialog methods IGNORE it (per stub) |
| `abortable` / `abort_autocheck` (get/set), `aborting` (read) | script abort switches |
| `progress_start(description, items, unit)` / `progress_step(item, completed)` / `progress_absolute(item, absolute_progress)` | GUI progress-bar chrome; no close member |
| `ui.*` | ALL modal (error/warning/info/prompt/select_many/choose/query_string/query_password/query_credentials/open/save_file_dialog/browse_directory_dialog/reset_stored_result) — wedge hazard G-08; confirm-gated `ecockpit_system_ui` |
| `exit(exitcode=0)` | shuts down e!COCKPIT **and the armed relay** — confirm-gated |
| `commands`, `factories` | command/factory inventories (surfaced by the info op) |

The executor keeps stub-derived value tables (`_PROMPT_HANDLING_FLAGS`,
`_PROMPT_RESULT_VALUES`, `_PROMPT_CHOICE_VALUES`, `_SEVERITY_VALUES`) and
prefers the injected enum globals (`PromptHandling`, `PromptResult`,
`PromptChoice`, `Severity`) at runtime — same stand-in pattern as
`PouType`/`DutType`.

## IScriptProject (e_projects.primary / open_project(...))

- `save()` — saves at its **physical location** (`path` property).
  Fails with *"Current project is stored with a temporary file-name"* when
  the GUI session holds the project unnamed/unusual.
- `save_as(path, password=None)` — saves under a **new filename**
  (RETARGETS the open project; password optional, re-encrypts when given).
  Avoid for snapshots — copy the file instead.
- `export_xml(reporter, objects, path, recursive)` — PLCopen XML export
  (→ `ecockpit_export`); the IScriptProject2 overload adds a 5th
  `export_folder_structure` bool (IScriptProject7 a 6th,
  `declarations_as_plaintext`). **`path` is the path of the FILE we
  export into — never a directory** (G-17); `path=None` returns the
  whole export as the return string — the executor's deterministic
  fallback writes it. `objects` takes a Python list of script objects
  (e.g. `[application]`, recursive). Reporter: subclass the injected
  **`ExportReporter`** (G-05) with `*argv` callbacks (G-16):
  `error/warning(message)`, `nonexportable(obj)`, `aborting` property.
- `save_archive(path)` — single-file `.projectarchive` backup
  (NO MCP tool — removed, see the verdict; G-14). **VERDICT (live 2026-10-21
  + 10-22 retest, e!COCKPIT 11.3.1.0): BROKEN in this build — do not
  rely on it.** Every binding NREs inside the engine with
  `Object reference not set`: the 1-arg form, `(path, None)`, the
  IScriptProject9 `(path, None, None, None)` (which IronPython first
  rejects with `Multiple targets could match` across the 7 overlapping
  IScriptProject8/9 overloads), the fully disambiguated
  `(path, "", ArchiveCategories.default|.none)` via the injected
  **`ArchiveCategories`** global, and the named-args IScriptProject9
  form — **retested AFTER the project was saved/named**: unlike
  `source_download` (which needed exactly that), the NREs persist, so
  the unnamed project explains `source_download` but NOT
  `save_archive`. The executor's ladder + attempt log stay (self-heals
  if WAGO fixes the engine); `ecockpit_snapshot` (plain `.ecp` copy —
  works live) is the backup path.
- `path` — the on-disk project path (prefer over disk-searching).
- `active_application`, `devices`, `find(name, recursive=True)`,
  `get_device(name)`, `get_children(...)`.

## Online (module `online`, IScriptOnlineApplication, device)

- `online.create_online_application(app)` → online app:
  `read_value("path.to.var")`, `create_boot_application()` (download while
  online), login/logout variants.
- **`device.connect()` DEPLOYS (G-09, live 2026-10-21)**: a plain connect
  performed an implicit login-WITH-DOWNLOAD of the open application
  during a read-only `plc_ls` — "days of untested code went live".
  FIXED: every online command now logs in via the
  `_online_login` ladder — `onlineapp.login(OnlineChangeOption,
  delete_foreign_apps)` with read-only commands preferring
  **`OnlineChangeOption.Keep`** (stub-only: "Do not online update. Do
  not download. Keep as it is."), deploy commands Try → Force → Never
  → connect fallback.
- `online.set_default_credentials(user, password)`; also
  `set_specific_credentials`, `clear_all_credentials`, `gateways`.
- `device.connect()` / `disconnect()` / `start()` / `stop()` /
  `is_simulated` / `ip_address` / `read_parameter(...)` /
  `write_parameter(...)`; `project.active_application` for the online app.
- `forced_disconnect()` (IScriptOnlineDevice2) — logout that cannot hang;
  exposed as `ecockpit_disconnect(force=true)`.
- `IScriptOnlineApplication.login(OnlineChangeOption, delete_foreign_apps)`
  — EXPOSED (live 2026-10-21): `ecockpit_login` (option
  `try`/`force`/`never`/`auto`, confirm-gated, session stays open), and
  it is the login path for every online command. `OnlineChangeOption`
  per CHM: `Never` (0, online change never → full download forced),
  `Try` (1, online change if possible else full download), `Force`
  (2, online change or nothing). Reflected live: `login`/`logout`
  exist on the online application. Background: plain
  `device.connect()` performed an implicit login-WITH-DOWNLOAD of the
  open application during a read-only `plc_ls` — the ladder
  (`_online_login`: Try → Force → Never → connect) replaced it.

### Prepared values — online writes (→ `ecockpit_write` / `ecockpit_unforce_all`)

`set_prepared_value(expression, value)` stages a write (value is an IEC
literal STRING: `"TRUE"`, `"42"`, `"'text'"`); then exactly one of:

- `write_prepared_values()` — one-shot write,
- `force_prepared_values()` — values stay forced,
- `unforce_all_values()` — release all forces.

Readbacks: `get_prepared_value(expression)`, `get_prepared_expressions()`,
`get_forced_expressions()` (currently forced variables) — EXPOSED as the
read-only `ecockpit_prepared_values` tool.
State properties: `is_logged_in`, `application_state` (ApplicationState),
`operation_state` (OperatingState), `timeout` (→ `ecockpit_state`).
`source_download()` writes the source project to the PLC — EXPOSED as
`ecockpit_source_download` (confirm-gated; distinct from
`ecockpit_download`, which deploys the compiled application).
**LIVE (G-04, G-11; 2026-10-21)**: the member exists, but NREs
("Object reference not set") when the project has UNSAVED changes —
`cmd_source_download` therefore runs `project.save()` first
(best-effort, same as the snapshot command), after which the same call
uploaded fine (user-verified).

### PLC file system (ScriptOnlineDevice via `online.create_online_device()` → `ecockpit_plc_ls/get/put/rm`)

**CORRECTED (2026-10-22, ScriptLib stubs at `...\CODESYS\ScriptLib\
Stubs\scriptengine\ScriptOnline.pyi`)**: the file API EXISTS — but on
the **`ScriptOnlineDevice` created by `online.create_online_device()`**,
NOT on the project-tree device object (`EScriptDevice` — 32 members,
none file-related; our first reflection round targeted the wrong
object). `OnlineChangeOption.Keep = 3` is also stub-only (not in the
CHM): "Try to login. Do not online update. Do not download. Keep as it
is." — the safe login for read-only commands.

`ScriptOnlineDevice` members (3.5.9.0+ for the file block; connect/
disconnect/connected/shared_connected/forced_disconnect also on it —
it keeps an internal connection, so **Dispose() it when done**):

- `get_file_list_of_directory(remote_directory)` → dir info objects
  (`name`, `is_directory`, `is_file`, `size`, `last_modification_time`)
- `download_file(local_file, remote_file, force_overwrite)` — PLC →
  host (local first!)
- `upload_file(remote_file, local_file, force_overwrite)` — host → PLC
- `delete_file(remote_file)`, `rename_file`, `create_directory`,
  `rename_directory`, `delete_directory(remote_directory, recursive)`
- `download_source(bCompact=False, additional_items=None)` — push the
  source archive TO the PLC (→ `ecockpit_source_download`)
- `upload_source(archive_path)` — read the source archive back
  (→ `ecockpit_upload_source`)
- plus `reset_origin()`, `create_user_management(...)`,
  `activate_license(...)`, `change_user_password(...)`,
  `current_logged_on_username`

EARLIER (still true): the project-tree `EScriptDevice` has no file
members, and `onlineapp.source_download()` (online APPLICATION object)
NREs — the executor's first plc_ls run failed on exactly that wrong
object.

`upload_source(archive_path)` (IScriptOnlineDevice3) reads the source
project archive back from the PLC (→ `ecockpit_upload_source`).

## Project tree, ST authoring, task/symbol config, device params

From the **ScriptLib stubs** (`...\CODESYS\ScriptLib\Stubs\scriptengine\
*.pyi` — the authoritative API surface, correcting the CHM in several
places; `OnlineChangeOption.Keep = 3` is stub-only). Implemented in
feat/scriptlib-tools:

- **Tree** (ScriptTreeObject/ScriptObject): `find(name, recursive)`,
  `get_children(recursive)`, `get_name()`, `rename(new_name)`,
  `remove()`, `move(new_parent, index)`, `create_folder(name)`;
  markers `is_device/is_application/is_folder/is_task_configuration/
  is_symbol_config/is_libman/is_textlist/is_imagepool/
  has_textual_declaration/has_textual_implementation` (direct access
  per the gotcha; `getattr`/`dir` unusable). → `ecockpit_tree`.
- **ST authoring** (ScriptIecLanguageObjectContainer, 3.5.9.0):
  `create_pou(name, type=PouType.Program|FunctionBlock|Function,
  language=ImplementationLanguages.st, return_type)`,
  `create_gvl(name)`, `create_dut(name, DutType..., baseType)`,
  `create_interface(name, baseInterfaces)`, and on POUs
  `create_method/create_property/create_action/create_transition`.
  Special POUs (3.5.12.10): `create_pou(SpecialPouType)` — implicit-
  check functions (check_bounds, check_pointer, check_range_unsigned/
  signed, check_div_real_64/32, check_div_int_64/32,
  check_l_range_unsigned/signed); the engine names the object itself.
  Stub warning, passed through to the tool result: *adding a check
  function will provoke a full recompile and prohibit an online
  change*. → `ecockpit_object_create` kind=special_pou.
  **Text** (ScriptTextualObject, 3.5.6.0): `textual_declaration` /
  `textual_implementation` → ScriptTextDocument with `.text`,
  `.replace(new_text=...)` (whole-part replace). → `ecockpit_object_create`,
  `ecockpit_textual_read/write`.
- **Task config** (ScriptTaskConfigObject, 3.5.10.0):
  `application.create_task_configuration()` → `create_task(name)` →
  `kind_of_task` (KindOfTask.Cyclic/Freewheeling/Event/ExternalEvent/
  Status/ParentSynchron), `interval`/`interval_unit`/`priority`
  (strings), `watchdog.{enabled,time,time_unit,sensitivity}`,
  `pous.add(name)`. → `ecockpit_task_config` / `ecockpit_task_create`.
- **Symbol config** (ScriptSymbolConfigObject, 3.5.10.0):
  `application.create_symbol_config(export_comments, support_opc_ua,
  client_side_layout_calculator)`; `get_all_signatures(compile=True)`
  → signatures → variables (`name`, `type`, `effective_access`) —
  the full variable inventory for OPC UA / online tooling. →
  `ecockpit_symbol_config_variables` / `ecockpit_symbol_config_create`.
  Options + schema (→ `ecockpit_symbol_config_options`):
  `content_feature_flags` get/set (SymbolConfigContentFeatureFlags:
  SupportOPCUA=1, IncludeComments=2, IncludeAttributes=4,
  IncludeTypeNodeAttributes=8, IncludeExecutables=16,
  UseEmptyNamespaceByDefault=32; Xml* variants above bit 16) with
  `effective_content_feature_flags` (compiler-version-aware),
  `symbol_attribute_filter_type` (none/All/SimpleIdentifiers/Prefix/
  Regex) + `symbol_attribute_filter_data`,
  `symbol_comment_filter_type` (none/NormalComments/DocuComments/
  Both/PreferNormal/PreferDocu), `enable_direct_io_access` +
  `check_effective_direct_io_access()`, and
  `get_symbol_configuration_xsd()` → UTF-8 XSD byte array (the schema
  published at 3s-software.com, as applicable to the current release).
  NOTE: the stub has NO literal `export_comments` member — comment
  export rides the IncludeComments/XmlIncludeComments flags.
- **Device scan** (ScriptOnline gateway stack, 3.5.8.0):
  `online.gateways` (+`gateway_drivers`),
  `gateway.perform_network_scan()` → targets (`device_name`, `address`,
  `vendor_name`, `device_id`...), `gateway.find_address_by_ip(ip,
  port=11740)`. → `ecockpit_device_scan`.
- **Device comm** (ScriptDeviceObject): `set_gateway_and_ip_address(gw,
  "ip:port")`, `set_gateway_and_address`, `set_gateway_and_device_name`,
  `get_device_communication_settings()` (with `scanned_*` fields). →
  `ecockpit_device_comm`.
- **Device info** (ScriptDeviceObject + ScriptDriverInfo, 3.5.9.0):
  `get_device_identification()` → DeviceId `{type, id, version}`
  (ctor `DeviceID(iType, stId, stVersion)`, injected global),
  `get_module_identification()` → str,
  `get/set_simulation_mode(bool)`, `driver_info` → ScriptDriverInfo
  (`bus_cycle_task_by_name/by_guid` + `set_bus_cycle_task(name|guid|
  task)`, `io_application` + `set_io_application(ScriptApplication)`,
  `update_ios_while_in_stop`, `behaviour_for_outputs_on_stop`
  (StopResetBehaviour: KeepCurrentValues=0/SetToDefault=1/
  ExecuteProgram=2), `user_program_for_stop_reset_behaviour`,
  `always_update_variables` (AlwaysUpdateVariablesMode: Disabled=1/
  OnlyIfUnused=2/AlwaysInBusCycle=3), `generate_force_variables`,
  `enable_diagnosis`, `show_io_warnings_as_errors`,
  `can_set_io_application`). → `ecockpit_device_info` (setters
  confirm-gated). **LIVE 2026-10-22**: identification
  (PFC200_2ETH_RS → `{4096, "1006 1201", 5.17.3.10}`), simulation and
  the driver-info dump verified; `always_update_variables` reads **0**
  on this device — not in the stub enum (G-18); `factories` (system
  info) is not enumerable live.
- **Device parameters** (ScriptDeviceParameters, 3.5.4.0):
  `device.device_parameters` / `device.connectors.by_id(id).
  host_parameters` → parameter set; elements expose `identifier`,
  `value`, `can_access_online`, `read_online_value(timeout)`,
  `write_online_value(value)`, enums (`allowed_values`), ranges,
  struct sub-elements. → `ecockpit_device_params` (write is
  confirm-gated, PLC-touching).
- **IO mappings** (ScriptDeviceParameters 3.5.8.0 +
  ScriptDeviceObject): elements carry `is_mappable_io` →
  `io_mapping` (ScriptIoMapping: `variable` get/set — unqualified
  creates a new variable, qualified maps to an existing one;
  `default_variable`; `mapping_creates_variable`;
  `maps_to_existing_variable`; `automatic_iec_address` get/set;
  `manual_iec_address` get/set — setting an address switches auto
  off, setting None switches it back on; the GETTER returns the
  automatic address while auto is on) and
  `dev.export_io_mappings_as_csv(file_path)` /
  `dev.import_io_mappings_from_csv(file_path)` (absolute host paths).
  → `ecockpit_device_io` (mappings inspection read-only;
  set_mapping / import_csv confirm-gated). **LIVE 2026-10-22**:
  export verified (10.9 KB CSV, module channels included);
  `export_io_mappings_as_csv` sees mappings a root-only script walk
  misses — mappings live on the plugged modules (G-19), so the tool
  walks every `is_device` in the tree (153 elements on s4). The
  mapped elements' `Id` reads -1 and connector `.id` is not readable
  live (guarded to "?").
- **Application** (ScriptApplication, 3.5.10.0 fields): `is_uptodate`,
  `is_online_change_possible`, `create_boot_application(path,
  update_compile_info=False, write_visu_files=False)` (offline boot
  file; `update_compile_info=True` BREAKS online-change compatibility),
  `create_task_configuration()`, `create_symbol_config(...)`. →
  `ecockpit_app_control`.
- **Headless** (ScriptSystem): `system.prompt_handling =
  PromptHandling.LogSimplePrompts` keeps simple prompts out of the GUI
  (the relay cannot answer dialogs); `system.delay(ms)` pumps the UI
  loop. Applied once per executor process.

## Libraries (global `librarymanager` — ILibManager)

*"An instance implementing this interface is injected into the scriptengine
scope under the name `librarymanager`."* — repository-level curation of
compiled libraries. CHM signatures, corrected where the live build
disagrees (**live-verified 2026-10-21**; the executor wraps every member
access in try/except per the gotcha above):

- `repositories` → `ILibRepository` list in **search order** (first match
  wins). It is a *snapshot* — refetch after insert/remove/move.
  `ILibRepository`: `name`, `root_folder`, `editable` (system
  repositories are not editable → cannot be removed).
- `get_all_libraries(exclude_shadowed_libs=True)` → all managed libraries;
  overload `get_all_libraries(repository)` scopes to one repo.
- `find_library(display_name)` — **live deviations**: only the FULL
  display name is accepted (`"NETWORK, 1.3.5.3 (OSCAT)"`; a bare title
  `"NETWORK"` raises `SystemError: stDisplayName`), and the return value
  is a flat list of `IManagedLib` objects — NOT `(IManagedLib,
  ILibRepository)` tuples.
- `get_library(name, repository=None)` → first `IManagedLib` by exact
  NAME. **Live deviations**: `repository=None` raises
  `TypeError: Value cannot be null. Parameter name: repo`, and a name
  that is not the full display name raises `SystemError: stDisplayName`
  (the NAME vs DISPLAY-NAME key split the CHM describes does not exist
  here).
- `install_library(filepath, repository=None, overwrite=False)` → the
  installed `IManagedLib`. **Live deviation**: `repository=None` raises
  the same null-argument TypeError — the "FIRST repository" default must
  be resolved by the caller. An install whose library already exists in
  the target repository fails natively (`IOError: Library already
  exists`) unless `overwrite=True`. → `ecockpit_lib_install`
  (confirm-gated when overwrite).

Because of the find/get deviations above, the executor resolves ALL
lookups by enumeration over `get_all_libraries()` in repository search
order (`_resolve_library_pairs`) and never calls find_library/get_library.
- `uninstall_library(repository, library)` → `ecockpit_lib_uninstall`
  (confirm-gated; ambiguity refused unless repo given).
- `insert_repository(rootfolder, name, index=-1)` — rootfolder must be an
  EXISTING directory; `remove_repository(repository, delete_on_disk=False)`;
  `update_repository(repository, new_name=None, new_location=None,
  copy_libraries=False)`; `move_repository(repository, new_index)` (-1 =
  end). → `ecockpit_repo_add/remove/update/move`.
- `get_file_path(IManagedLib)` → repository file path.
- `IManagedLib` properties: `displayname`, `version`, `company`,
  `default_namespace`, `title`, `dependencies` (string list),
  `categories` (`ILibCategory` list).
- `categories` / `top_level_categories` (ILibCategory) — not exposed as
  tools yet.

**There is no per-application Library Manager scripting API in this
build**: the e!COCKPIT UI shows a Library Manager under each application,
but script access to its *referenced* libraries is not part of this
surface — the `librarymanager` global covers repository curation only.

## Not in this build (probed live — do not retry)

`system.get_message_log`, `system.get_messages_count`,
`project.get_active_application`, `project/Application.get_build`,
`application.check_all` (not on the wrapper), `Application.activate_application`,
`project.get_name` (but `Application.get_name` EXISTS).
The widely-copied "CODESYS V3.5 scripting" web article describing
`build.execute()`/`has_errors()` does not match this API.

The full CHM surface (217 interfaces, ~580 methods / ~830 properties) vs
what this toolset deliberately exposes is inventoried in
[SCRIPTENGINE_NOT_EXPOSED.md](SCRIPTENGINE_NOT_EXPOSED.md).
