# Testing — live-environment results

One row per live run against the real e!COCKPIT + PLC. This is the
contract between "the stubs say so" and "it actually works".
**Comprehensive testing pass is planned separately** — until then this
table is the authority; rows are append-only (retests add new rows).

Status legend: `VERIFIED` (works, evidence recorded) ·
`FAILED-DIAGNOSED` (fails; root cause + fix/ref verdict linked) ·
`PENDING` (implemented, not yet run live).

Rules: read-only first; destructive/download runs only on an explicit
owner request; record engine NREs as verdicts (G-07) with the
SCRIPTENGINE_API.md cross-reference.

| Date | Tool / command | Key args | Status | Notes |
|---|---|---|---|---|
| 2026-10-21 | ecockpit_build | defaults | VERIFIED | 82 imports, 0 errors (pre-packaging log) |
| 2026-10-21 | ecockpit_export | dir=default | VERIFIED | 761 KB `Application.xml` (mode=file) + Library Manager XML; needed ExportReporter name fix (G-05), *argv callbacks (G-16), file-path contract (G-17) |
| 2026-10-21 | ecockpit_libs / repos / lib_find | repo=oscat; name="network" | VERIFIED | oscat (5 libs) + System (3933); shadowed flags correct; bare-title search works (enumeration lookups) |
| 2026-10-21 | ecockpit_login ladder | via any online command | VERIFIED | `login(OnlineChangeOption.Try)` in 15 s; old connect did an implicit download (G-09 incident) |
| 2026-10-21 | ecockpit_prepared_values | {} | VERIFIED | 0 staged / 0 forced (idle PLC); all three readbacks functional |
| 2026-10-21 | ecockpit_save_archive | 6 ladder forms | FAILED-DIAGNOSED | NREs in every binding, pre- AND post-save (G-14); tool REMOVED; use ecockpit_snapshot |
| 2026-10-21 | ecockpit_source_download | confirm=true | FAILED-DIAGNOSED | device `download_source` NRE (engine); app fallback hit a locked Archive.prj staging file (environmental); retry pending |
| 2026-10-22 | ecockpit_plc_ls | remote="/" | VERIFIED | "/" refused (G-10); fallback listed `.` — 7 entries incl. PlcLogic/ (create_online_device ladder) |
| 2026-10-22 | ecockpit_tree | op=list recursive=false | VERIFIED | real project tree: PFC200_2ETH_RS device, Library Manager, Project Information |
| 2026-10-22 | ecockpit_task_config | op=inspect | VERIFIED | Monitor task (POUs S/A/D/M, prio 14, t#50ms) + second task |
| 2026-10-22 | ecockpit_device_scan | op=gateways | VERIFIED | WACDefaultGateway; drivers SharedMemory/TCP-IP |
| 2026-10-22 | ecockpit_project_control | op=info | VERIFIED | s4-v3.10.0-pre.ecp, dirty=false |
| 2026-10-22 | ecockpit_plc_fs_ops | op=ls, remote="/" | VERIFIED | tool now accepts ls (read-only); connected + listed `.` — 7 entries via `create_online_device.get_file_list_of_directory` |
| 2026-10-22 | ecockpit_device_params | op=list / read online=true | PENDING | read-only; run in the next armed session |
| 2026-10-22 | ecockpit_symbol_config_variables | compile=true | PENDING | builds first (slow but safe) |
| 2026-10-22 | ecockpit_system | op=info / write_message / set_prompt_answer / prompt_answers / set_flags | PENDING | read-only + message-store ops (feat/system-device-io) |
| 2026-10-22 | ecockpit_system_ui | op=ui (any dialog) / op=exit | PENDING | DESTRUCTIVE — modal wedge / kills e!COCKPIT; owner request only |
| 2026-10-22 | ecockpit_device_info | op=identification / simulation / driver_info | PENDING | read-only (feat/system-device-io) |
| 2026-10-22 | ecockpit_device_info | op=simulation_set / driver_info_set | PENDING | DESTRUCTIVE — project device configuration; owner request only |
| 2026-10-22 | ecockpit_device_io | op=mappings / export_csv | PENDING | read-only (feat/system-device-io) |
| 2026-10-22 | ecockpit_device_io | op=set_mapping / import_csv | PENDING | DESTRUCTIVE — modifies the IO configuration; owner request only |
| 2026-10-22 | ecockpit_symbol_config_options | op=config / xsd | PENDING | read-only (feat/system-device-io) |
| 2026-10-22 | ecockpit_symbol_config_options | op=config_set | PENDING | DESTRUCTIVE — project symbol config; owner request only |
| 2026-10-22 | ecockpit_object_create | kind=special_pou (any check function) | PENDING | DESTRUCTIVE — forces a full recompile, disables online change; owner request only |
| 2026-10-22 | ecockpit_system | op=info | VERIFIED | ui_present=true, prompt_handling decoded [LogSimplePrompts], abort flags, commands inventory (capped 50); factories NOT enumerable live (factory_count null) — armed-session pass, worktree relay |
| 2026-10-22 | ecockpit_device_info | op=identification | VERIFIED | PFC200_2ETH_RS: DeviceId {type 4096, id "1006 1201", version 5.17.3.10}; module_id "" (none, per stub semantics) |
| 2026-10-22 | ecockpit_device_info | op=simulation | VERIFIED | false (live device, not simulated) |
| 2026-10-22 | ecockpit_device_info | op=driver_info | VERIFIED | no bus cycle task configured (empty name, zero guid); io_application guid set; **always_update_variables reads 0 — the stub enum has no 0 (G-18)** |
| 2026-10-22 | ecockpit_device_io | op=mappings | VERIFIED | 153 mappable elements across the whole tree (Kbus, DI1_16_PB_classic, ...). First run found 0 — mappings live on the PLUGGED MODULES, a root-only walk misses them (fixed same commit: walk every is_device). Connector .id reads fail live → "source: connector ?" (guarded) |
| 2026-10-22 | ecockpit_device_io | op=export_csv | VERIFIED | 10,875 bytes → `<ECP_PROJECT_ROOT>/io-mappings/PFC200_2ETH_RS_io_mappings.csv`; engine's own export confirmed the module-channel mappings the script walk originally missed |
| 2026-10-22 | ecockpit_symbol_config_options | op=config / op=xsd | VERIFIED (precondition path) | clean error: no symbol configuration object under Application — create it first (ecockpit_symbol_config_create, destructive, owner request); op=config/xsd untested until then |
| 2026-10-22 | ecockpit_device_params | op=list | VERIFIED | root device: 4 params (Processor Load * set); parameter-set iteration confirmed live (was PENDING from the ScriptLib round; read online=true stays PENDING) |
| 2026-10-22 | ecockpit_snapshot | defaults | VERIFIED | `<snapshots>/..._3.10.0-pre7.ecp` taken BEFORE the test round; note: the GUI session holds the project as an AUTOSAVE (`...ecp.autosave`), so project.path is the autosave and discovery found the real .ecp |
| 2026-10-22 | ecockpit_snapshot | after owner's manual reload | VERIFIED | naming fixed: `_display_name`'s getattr probe never ran on the wrappers (G-01) and EScriptProject exposes neither get_name nor .name — the prefix is now the on-disk project FILE base (autosave paths excluded): **`s4-v3.10.0-pre_3.10.0-pre7.ecp`** (was `Wago.Eng...EScriptProject_3.10.0-pre7.ecp`) |
| 2026-10-22 | (owner evidence) | manual snapshot reloaded | RESOLVED | the owner loaded their manual .ecp snapshot and it compiles GREEN — the 5 C140/C418 errors were the OLD GUI session's in-memory state, not any file state; the session additions (Symbols symbol config) went with the rollback and can be re-created on request |
| 2026-10-22 | ecockpit_system | write_message / set_prompt_handling / prompt_answers / set_flags / progress | VERIFIED | combo `LogSimplePrompts+LogMessageKeys` set + read back, and the NEXT command showed the revert to LogSimplePrompts (per-command lifetime proven); prompt_answers persisted ACROSS commands (set → read on next cmd → clear); flags read back; progress start/step/absolute OK |
| 2026-10-22 | ecockpit_messages / clear_messages | round-trip | VERIFIED | write → categories walk → clear → 5 categories cleared, 0 failed |
| 2026-10-22 | ecockpit_device_comm | op=get | VERIFIED | structured device_address `038D.1000.2DDC.C0A8.0F32`, gateway guid; scanned_* empty (manually configured device) |
| 2026-10-22 | ecockpit_device_scan | op=address / op=scan | VERIFIED | 192.168.15.50 → the exact configured structured address (cross-check); scan ran, 0 new targets; gateway labels fixed to gw.name (interop repr has no Name= slice) |
| 2026-10-22 | ecockpit_device_params | op=read (offline) | VERIFIED after 2 live-bug fixes | value "80", id 10001000, can_access_online=true. Fix 1: `params[name]` hit the .NET positional indexer (G-23) → enumerate by name. Fix 2: the parameter's element iteration raised → the parameter doubles as its own element |
| 2026-10-22 | ecockpit_textual_read / textual_write | scratch POU round-trip | VERIFIED | GVLMqtt declaration read (sAppVersion 3.10.0-pre7); write→read-back identical on the scratch POU |
| 2026-10-22 | ecockpit_app_status | before/after special_pou | VERIFIED | baseline: active, uptodate=false, **online_change=true**; after create_pou(SpecialPouType.check_range_signed): **online_change=FALSE**; after remove + rebuild: **true again** — the stub warning, live-proven in both directions |
| 2026-10-22 | ecockpit_symbol_config_create | confirm=true | VERIFIED | "Symbols" created; needed the G-24 ladder (calculator param is a System.Guid — False/None/str rejected, 2-arg absent; CLR Guid.Empty rung worked) |
| 2026-10-22 | ecockpit_symbol_config_options | op=config / config_set | VERIFIED | flags decoded (set SupportOPCUA+IncludeComments+IncludeAttributes → effective read-back → reverted); comment filter set + reverted to none (needed G-20: live enum lacks 'none', typed property rejects ints → CLR Enum.ToObject) |
| 2026-10-22 | ecockpit_symbol_config_variables | compile=true | VERIFIED | real signatures (BPLog.Variables, ...); the build runs first |
| 2026-10-22 | ecockpit_symbol_config_options | op=xsd | VERIFIED | 34,295 B → `<ECP_PROJECT_ROOT>/plcopensrc-export/Symbolconfiguration.xsd` |
| 2026-10-22 | ecockpit_tree / object_create | folder + pou(+ST text)/gvl/dut/interface, rename, move, remove | VERIFIED | scratch folder + 4 objects created under Application, renamed, moved, ST write/read-back, then ALL removed (tree search → 0); needed G-22 (project has no create_pou → container defaults to the application) |
| 2026-10-22 | ecockpit_project_control | op=info_set set+clear | VERIFIED | description set → read back → cleared → read back; info shows the autosave path + dirty=true |
| 2026-10-22 | ecockpit_device_info | op=simulation_set / driver_info_set | VERIFIED | simulation true→read→false→read (reverted); driver_info no-op writes (current values) applied + read back |
| 2026-10-22 | ecockpit_device_io | op=set_mapping | VERIFIED + INCIDENT | the unfiltered no-op set clobbered all 16 channel variables of DI1_16_PB_classic._IN (G-21) — repaired by the import below; set_mapping now refuses >1 element without element=/all_elements=true |
| 2026-10-22 | ecockpit_device_io | op=import_csv + export round-trip | VERIFIED | importing the pre-damage export restored all 16 original variables (walk-verified); re-export after repair was BYTE-IDENTICAL (10,875 B, diff clean) |
| 2026-10-22 | ecockpit_object_create | kind=special_pou (check_range_signed) | VERIFIED | engine-named `CheckRangeSigned` created under Application; forced full rebuild ran; online change flipped false (see app_status row); removed + rebuilt → true again. KEPT OUT: the project was left WITHOUT the check POU |
| 2026-10-22 | ecockpit_build / rebuild | skip_import=true | FAILED-DIAGNOSED | 5 PRE-EXISTING compile errors in owner POUs (C140 x2 mBuildDiscovery/mBuildState, C418 x3 sPayload→Fb_JSON_Writer_02) — present before the round (app_status uptodate=false baseline); NOT caused by the session (all scratch objects removed; errors reference untouched POUs). Owner attention needed |
| — | ecockpit_system_ui ui.* / exit; prompt_answers end-to-end vs a real prompt; device_params read online=true; write/unforce; download/stop/app_control activate|start|reset; plc_put/get/rm/ops; device_comm set_ip; task_create (no delete op — would pollute); lib_install/uninstall + repo_* (machine-wide); upload_source; source_download retry | PENDING | STOPPED BEFORE PLC-TOUCHING per owner instruction 2026-10-22 |
| — | ecockpit_app_control op=activate/boot/start/reset | confirm=true | PENDING | DESTRUCTIVE — owner request only |
| — | ecockpit_download | confirm=true | PENDING | DESTRUCTIVE — replaces the RUNNING app; owner request only |
| — | ecockpit_device_params op=write | confirm=true | PENDING | DESTRUCTIVE — PLC parameter write; owner request only |
| — | ecockpit_object_create / textual_write / task_create / symbol_config_create / tree rename|remove|move|folder / plc_fs_ops rename|mkdir|rmdir / device_comm set_ip / project create|open|open_archive|close | confirm=true | PENDING | project-modifying; owner request only |
