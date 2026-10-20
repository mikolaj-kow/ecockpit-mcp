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
- `path` — the on-disk project path (prefer over disk-searching).
- `active_application`, `devices`, `find(name, recursive=True)`,
  `get_device(name)`, `get_children(...)`.

## Online (module `online`, IScriptOnlineApplication, device)

- `online.create_online_application(app)` → online app:
  `read_value("path.to.var")`, `create_boot_application()` (download while
  online), login/logout variants.
- `online.set_default_credentials(user, password)`; also
  `set_specific_credentials`, `clear_all_credentials`, `gateways`.
- `device.connect()` / `disconnect()` / `start()` / `stop()` /
  `is_simulated` / `ip_address` / `read_parameter(...)` /
  `write_parameter(...)`; `project.active_application` for the online app.
- `IScriptOnlineApplication.login(OnlineChangeOption, delete_foreign_apps)`
  exists per docs (richer than the WAGO samples show) — candidate for
  explicit online-change control later.

## Not in this build (probed live — do not retry)

`system.get_message_log`, `system.get_messages_count`,
`project.get_active_application`, `project/Application.get_build`,
`application.check_all` (not on the wrapper), `Application.activate_application`,
`project.get_name` (but `Application.get_name` EXISTS).
The widely-copied "CODESYS V3.5 scripting" web article describing
`build.execute()`/`has_errors()` does not match this API.
