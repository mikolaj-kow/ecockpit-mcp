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
| — | ecockpit_app_control op=activate/boot/start/reset | confirm=true | PENDING | DESTRUCTIVE — owner request only |
| — | ecockpit_download | confirm=true | PENDING | DESTRUCTIVE — replaces the RUNNING app; owner request only |
| — | ecockpit_device_params op=write | confirm=true | PENDING | DESTRUCTIVE — PLC parameter write; owner request only |
| — | ecockpit_object_create / textual_write / task_create / symbol_config_create / tree rename|remove|move|folder / plc_fs_ops rename|mkdir|rmdir / device_comm set_ip / project create|open|open_archive|close | confirm=true | PENDING | project-modifying; owner request only |
