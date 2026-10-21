# e!COCKPIT ScriptEngine API — verified reference

Distilled from the official help file shipped with this install:

```
D:\Program Files (x86)\WAGO Software\e!COCKPIT\3S CODESYS\CODESYS\Online Help\en\ScriptEngine.chm
```

Decompile with: `7z x ScriptEngine.chm -o%TEMP%\scriptengine_chm`
(~2200 HTML topics; namespace `_3S.CoDeSys.ScriptEngine.BasicFunctionality`,
ScriptEngine.dll 3.5.11.1, IronPython 2.7 runtime). Everything below was
additionally verified LIVE in e!COCKPIT 11.3.1.0 on 2026-10-20.

## The one big gotcha

**The `getattr()` builtin FAILS on the .NET interop objects** (project,
Application, device, message objects) — even for members that work fine via
direct attribute access. `getattr(app, "create_boot_application")` raises
while `app.create_boot_application(path)` works. Never write probe helpers
based on getattr/dir(): `dir()` is EMPTY on these dynamic wrappers too.
Use direct attribute syntax inside try/except, and the documented names
from the CHM.

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
  export into — NEVER a directory** (live 2026-10-21: passing a
  directory makes .NET try to open it as a file → `Access to the path
  '<dir>' is denied` on any directory). If `path` is null the WHOLE
  export comes back as the return string ("The exported XML as string,
  or null if a filepath is given") — the executor's deterministic
  fallback: call with `path=None`, write the file itself. `objects`
  takes a Python list of script objects (e.g. `[application]`,
  recursive). Reporter callbacks: `error(message)`,
  `warning(message)`, `nonexportable(obj)`, `aborting` property.
  **EXPOSURE-NAME GOTCHA (live 2026-10-21)**: the reporter
  class must subclass the interface under its exposed script-scope name
  **`ExportReporter`** — `IExportReporter` is only the .NET type name and
  is never injected; subclassing a plain-`object` placeholder named
  IExportReporter made export_xml reject the reporter with
  `TypeError: expected IExportReporter, got Object_1$1`. (The import
  side is lucky: `IImportReporter` is exposed as `ImportReporter`, which
  is the name the executor always guarded on.) **CALLBACK ARITY GOTCHA
  (live 2026-10-21)**: the EXPORT flow calls `warning()` with TWO
  message arguments (`warning() takes exactly 2 arguments (3 given)`
  with the CHM's 1-arg signature) — the executor's export callbacks
  take `*argv` and log everything.
- `save_archive(path)` — single-file `.projectarchive` backup
  (NO MCP tool — removed, see the verdict). **VERDICT (live 2026-10-21, e!COCKPIT
  11.3.1.0): BROKEN in this build — do not rely on it.** Every binding
  NREs inside the engine with `Object reference not set`: the 1-arg
  form, `(path, None)`, the IScriptProject9 `(path, None, None, None)`
  (which IronPython first rejects with `Multiple targets could match`
  across the 7 overlapping IScriptProject8/9 overloads), and the fully
  disambiguated `(path, "", ArchiveCategories.default|.none)` via the
  injected **`ArchiveCategories`** global. Binding quirks worth
  remembering: IronPython needs a TYPED argument in the category slot
  (None leaves multiple overloads matching; a lone category into
  `IEnumerable` slots casts badly). The executor's `save_archive`
  command keeps the probe ladder + full attempt log so a fixed engine
  self-heals, and points at `ecockpit_snapshot` (plain `.ecp` copy —
  works live) for
  backups today. The MCP tool is **disabled at the server layer** — it
  returns the explanation without touching the relay (the executor
  ladder remains for file-protocol users and self-heals if the engine
  gets fixed).
- `path` — the on-disk project path (prefer over disk-searching).
- `active_application`, `devices`, `find(name, recursive=True)`,
  `get_device(name)`, `get_children(...)`.

## Online (module `online`, IScriptOnlineApplication, device)

- `online.create_online_application(app)` → online app:
  `read_value("path.to.var")`, `create_boot_application()` (download while
  online), login/logout variants.
- **`device.connect()` DEPLOYS (live 2026-10-21)**: a plain connect
  performed an implicit login-WITH-DOWNLOAD of the open application
  during a read-only `plc_ls` — "days of untested code went live".
  Every `_online_connect` caller (plc_ls/get/put/rm, read, write,
  state, download, …) must be treated as potentially deploying until
  an explicit non-deploying login (`onlineapp.login(OnlineChangeOption,
  delete_foreign_apps)` ladder) is found and wired in.
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
**LIVE 2026-10-21**: the member exists but the engine NREs
("Object reference not set") — same defect class as `save_archive`.
Treat as broken in this build; the tool stays (confirm-gated) so a
fixed engine self-heals.

### PLC file system (IScriptOnlineDevice4 → `ecockpit_plc_ls/get/put/rm`)

**VERDICT (live 2026-10-21, CLR reflection): the PLC file API does not
exist in this build.** The online device (32 members) and online
application (34 members) contain **no** file/transfer members at all —
`get_file_list_of_directory`, `download_file`, `upload_file`,
`delete_file` are all absent, and the only file-ish member anywhere is
`source_download` (the source-archive push, deliberately not a tool).
Discovery method: `dir()` is empty and `getattr()` lies on the interop
wrappers, so the executor reflects via `clr.GetClrType(obj).
GetMethods()/GetProperties()` — see `_reflect_member_names`.
`ecockpit_plc_ls/get/put/rm` therefore cannot work in this build
(their ladder + surface + reflection probe stays in the executor so a
fixed engine self-documents). `ecockpit_upload_source` is equally
suspect (`upload_source` absent from the reflected surface too).
The online application's FULL reflected surface (34): application,
application_state, create_boot_application, force_prepared_values,
get_application, get_application_state, get_forced_expressions,
get_is_logged_in, get_online_device, get_operation_state,
get_prepared_expressions, get_prepared_value, get_timeout,
is_logged_in, login, logout, operation_state, read_value, read_values,
reset, set_prepared_value, set_timeout, set_unforce_value,
source_download, start, stop, timeout, unforce_all_values,
write_prepared_values (+ Dispose/Equals/GetHashCode/GetType/ToString).

CHM documentation (unusable in this build):

- `download_file(local_file, remote_file, force_overwrite)` — PLC → host
  (local first!)
- `upload_file(remote_file, local_file, force_overwrite)` — host → PLC
  (remote first!)
- `get_file_list_of_directory(remote_dir)` → `IScriptDirectoryInfo[]`
  (`name`, `is_directory`, `is_file`, `size`, `last_modification_time`)
- `delete_file(remote_file)`

`upload_source(archive_path)` (IScriptOnlineDevice3) reads the source
project archive back from the PLC (→ `ecockpit_upload_source`).

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
