# Test plan — what is verified, what is next

Companion to [TESTING.md](TESTING.md) (append-only live results — the
authority) and [SUMMARY.md](SUMMARY.md) (per-capability status). Two
kinds of "tested":

- **Offline** (34 pytest cases): command logic, gates, enum decoding,
  dispatch — against fake engine objects. Runs in CI on every push.
- **Live** (TESTING.md rows): the real engine + PLC. Read-only rows run
  in any armed session; destructive rows only on an explicit owner
  request ("owner go"), one batch at a time.

State as of 2026-10-22, feat/system-device-io.

**Progress (armed rounds 2-3, 2026-10-22):** A1, A2, B, C, D1 and E
are DONE and live-verified — including the special_pou online-change
flip (true → false → true across create/remove), the G-21 set_mapping
incident with the CSV-round-trip repair, all six ui.* dialogs with the
owner answering, and `system exit` as the session's final command (see
TESTING.md). The owner's manual snapshot compiles GREEN — the 5
C140/C418 errors were the old GUI session's in-memory state (resolved);
the in-session "Symbols" symbol config went with that rollback.
REMAINING: D2 rest (variable write + unforce on the RUNNING app, PLC
file-op batch, prompt_answers end-to-end via plc_put without force,
stop/reset — deliberately last), D3-follow-up (keep a special_pou),
F (machine-wide). DONE in the D2 ramp: state/prepared_values (in-sync
and out-of-sync guard paths), download + start, **ONLINE CHANGE**
(body edit → login Try → app stayed running), device_params online
read (times out on parameters even running — target limitation).
The McpOnlineProbe FB is intentionally left in the project (the
online-change test object; remove via GUI or a later round).
Snapshot naming fixed: `s4-v3.10.0-pre_<version>.ecp`.

**INCIDENT (2026-10-22, G-26, FIXED):** the first D2 ramp (state /
prepared_values / plcfs ls — all intended read-only) DEPLOYED the open
project to the PLC: the login ladder escalated past a failed Keep, and
`create_online_device`'s connect is itself a login-with-download.
Impact: the PLC now runs the owner's green snapshot (the app it replaced
was STOPPED; nothing was started). Both holes closed — read-only logins
are Keep-only and refuse to escalate; the online-device connect refuses
when `is_uptodate=false` without confirm. D2 ramp results: state and
prepared_values VERIFIED; device_params online read times out while the
app is STOPPED (state limitation); plc_ls verified.

## Live-verified so far

| Domain | Verified |
|---|---|
| Build/IO | build (structured harvest), export (file + string forms + LM XML), snapshot, plc_ls + plc_fs_ops ls (G-10 root `.`) |
| Online (safe half) | login ladder (`Try` 15 s; `Keep` for read-only), prepared_values, device_params list + offline read |
| Tree/config authoring | tree folder/rename/move/remove, object_create pou/gvl/dut/interface (+ST text), textual write/read round-trip, project info_set, symbol_config create + options set/revert + variables (compile=true) + xsd |
| Libraries | libs (paging/filter), repos, lib_find (display-name quirks) |
| Device discovery | device_scan gateways/scan/address, device_comm get |
| Device config writes | simulation_set + revert, driver_info_set, set_mapping (element-scoped), import_csv (byte-identical round-trip) |
| New round read-only | system info/message/prompt-simulation/flags/progress, device_info identification/simulation/driver_info, device_io mappings (153) |
| Special POUs | special_pou create + the online-change flip, both directions |
| Broken (final verdicts) | save_archive — engine NRE, tool removed (G-14); onlineapp.source_download — wrong object (G-04); device retry pending |

## Remaining, by phase

| Phase | Scope | Risk / preconditions | Owner go? |
|---|---|---|---|
| A1 | Daemon restart → smoke_client: tools/list shows **54**, all confirm gates refuse structured | host-side only; the RUNNING daemon still registers 49 tools (4 new ones are relay-live but not MCP-visible until restart) | no |
| D2 | PLC-touching: device_params read online=true, device_params write (benign param), write (one harmless variable) + unforce_all, app_control status/activate/start, plc_get (read), plc_put/mkdir/rename/rmdir/rm on a scratch PLC dir, download (replaces the RUNNING app — schedule deliberately), stop, device_comm set_ip | touches the RUNNING PLC | yes, per op |
| D3-follow-up | special_pou variant left IN the project (this round created + removed it) — only with a deliberate full-rebuild schedule | irreversible online-change loss while present | yes, explicit |
| E | Wedge/last: ui.* dialogs owner answers at the GUI (info→OK, query_string, open_file_dialog→Cancel, prompt→Yes), set_prompt_handling ForwardSimplePrompts + revert, prompt_answers end-to-end against a prompt-issuing engine call (candidate: an overwrite prompt during plc_put without force), **exit very last** (kills e!COCKPIT + relay) | modal dialogs can wedge the relay if unanswered (G-08) | yes, at GUI |
| F | Machine-wide + environmental: lib_install/uninstall (scratch lib), repo_add/remove/update (scratch repo), repo_move (reversible), upload_source (never run on the corrected object), source_download retry (Archive.prj staging lock), probe, task_create (no delete op — creates permanent scratch, decide where) | changes machine-wide library state / PLC archives | yes |

## Rules

- One TESTING.md row per run, same-commit doc updates; new traps become
  `G-nn` rows in GOTCHAS.md in the same commit.
- Destructive ops carry the MCP-layer refusal + executor gate — Phase A1
  re-verifies the refusals for the 4 new tools.
- `exit` is always the final command of a session, never mid-plan.
- A failed destructive op stops its phase; diagnose before retry (G-07:
  no blind retries).
- Snapshot before any project-modifying phase (done for this round).
