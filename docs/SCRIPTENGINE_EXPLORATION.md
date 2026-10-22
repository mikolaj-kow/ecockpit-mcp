# Exploring the ScriptEngine API — how-to for future agents

The workflow and sources of truth for discovering what the e!COCKPIT
ScriptEngine can do, verifying it live, and turning findings into
tools. Companion docs: [SCRIPTENGINE_API.md](SCRIPTENGINE_API.md)
(verified reference), [SCRIPTENGINE_GOTCHAS.md](SCRIPTENGINE_GOTCHAS.md)
(trap table, IDs `G-nn`), [SUMMARY.md](SUMMARY.md) (all functions:
done + possible), [TESTING.md](TESTING.md) (live results),
[ROADMAP.md](ROADMAP.md) (what remains).

## Sources of truth (authority order)

1. **Live probing** — the relay re-execs `executor.py` per command, so
   executor changes go live without re-arming. Probe recipe (file
   protocol, no MCP needed; paths relative to the repo root):

   ```bash
   WD="src/ecockpit_mcp/tmp"
   printf '%s\n' '{"id": "probe-1", "cmd": "<command>", "args": {}}' \
       > "$WD/ecockpit_cmd.json"
   # poll "$WD/ecockpit_cmd_result.json" for the matching id
   ```

   Discipline: read-only first; destructive/download only on an owner
   request; record every result in TESTING.md.
2. **ScriptLib stubs** —
   `ECP_INSTALL_DIR\3S CODESYS\CODESYS\ScriptLib\Stubs\scriptengine\*.pyi`
   (29 files, ~14k lines). The
   authoritative member lists: `ScriptOnline.pyi` (online app + device +
   gateways), `ScriptProject(s).pyi`, `ScriptObject.pyi`/`ScriptTreeObject.pyi`
   (tree + build properties + native IO), `ScriptIecLanguageObjectContainer.pyi`
   (POU/GVL/DUT creation), `ScriptTextualObject.pyi` (ST text),
   `ScriptTaskConfigObject.pyi`, `ScriptSymbolConfigObject.pyi`,
   `ScriptDeviceObject.pyi`/`ScriptDeviceParameters.pyi` (device tree +
   parameters), `ScriptLibManObject.pyi` (library manager + in-project
   references), `ScriptUserManagement*.pyi`, `ScriptTextListObject.pyi`,
   `ScriptImagePoolObject.pyi`, `ScriptExternalFileObject.pyi`,
   `ScriptSystem.pyi` (messages/UI/headless).
3. **CHM** — decompile once, then grep:
   `7z x "...\Online Help\en\ScriptEngine.chm" -o<dir>` (2048 topics).
   Weaker than the stubs: it documents a generic CODESYS ScriptEngine
   and disagrees with the live build in places (G-13).
4. **CLR reflection** — the only runtime discovery on the interop
   wrappers: `clr.GetClrType(obj).GetMethods()/GetProperties()`.
5. `ecockpit_probe` — legacy dump; getattr/dir-based, so of limited use
   (G-01/G-02).

### License findings (checked 2026-10-22)

- The stubs are **vendor-shipped for developer tooling** (README.txt:
  "This directory's parent should be added to your
  Python-development-engine's stub path"). No license/copyright headers
  in any `.pyi`. Using them as an API reference is their intended
  purpose.
- They remain "supplied Software" under the CODESYS engineering EULA —
  **do not redistribute the stub files themselves**; documenting API
  facts (signatures, behaviour) in this repo is fine.
- The CHM contains **no** copyright or terms text (verified: zero hits
  across all 2048 topics); the CODESYS EULA text in this install has no
  decompilation/reverse-engineering clause. Keep decompiled content
  internal; do not bulk-commit CHM output.
- **WAGO SW-License reviewed (2026-10-22, `SW-License_(INT_1.0_EN_
  06-2023)`, owner-extracted to local `tmp-eula.txt` — kept out of
  git)**. §3 usage restrictions: (a) no copying/duplicating the
  Software, (b) no public reproduction or making it available, (c) no
  "modify, translate, analyze, decode, convert or disassemble the
  source code of the Software", (d) no derivative works, (f) no passing
  the Software to third parties. Factual reading for this repo's
  workflow: the **stubs are provided for developer tooling** (their
  stated purpose), API **facts** recorded in our own words are not a
  reproduction, and the decompiled CHM + stub files + EULA text stay
  **local and out of git**. Broad clause (c) ("analyze... the
  Software") is the reason the CHM/stub contents must not be pushed
  publicly; if the docs ever leave this private repo, get a legal
  review first.

## Workflow (stub → tool)

1. Read the stub file for the domain; note exact signatures, param
   names, `:version added:` and the "injected under the name X"
   docstrings.
2. Cross-check the CHM topic (they diverge — see GOTCHAS G-13) and the
   verified sections of SCRIPTENGINE_API.md.
3. Implement in `executor.py`: a `cmd_<name>(project, args)` following
   the house patterns — direct attribute access only (G-01/G-02),
   guarded member reads, confirm gates for project/PLC-modifying ops,
   `KNOWN_COMMANDS` + dispatch entry, stand-in for any newly used
   injected global (`if "X" not in globals(): X = None`).
4. Server: one `@mcp.tool` per operation (`_run(cmd, args, timeout_s)`),
   `readOnlyHint`/`destructiveHint` annotations, MCP-layer refusal for
   gated ops, `_TIMEOUTS` entry.
5. Offline test where logic allows (fake objects injected into the
   executor module; import with `ECP_NO_RUN=1`).
6. Live-verify read-only paths via the file-protocol probe; record in
   TESTING.md. Destructive paths stay untested until the owner asks.
7. Update SUMMARY.md (status flip possible → tool) and
   SCRIPTENGINE_API.md.

## Injection names + stand-ins

Injected globals (stand-ins in `executor.py` keep the module
lintable/importable under plain CPython — `if "X" not in globals():
X = None`): `system`, `e_projects` (+ `projects` alias, resolved by
`_projects_global()`), `online`, `librarymanager`,
`ArchiveCategories`, `OnlineChangeOption` (Never/Try/Force/Keep),
`Severity`, `ImportReporter`/`ExportReporter` (exposure names! G-05),
`ConflictResolve`, `PromptHandling`, `PromptResult`, `PromptChoice`,
`PouType`, `DutType`, `KindOfTask`, `ImplementationLanguages`,
`ResetOption`. (`DeviceID`/`device_repository` are engine-injected
names too, but only needed by the not-yet-implemented device-tree
authoring — no stand-ins yet.) Engine enums: use direct member access
(`OnlineChangeOption.Try`), guarded.

Caution: `import ecockpit... executor` under CPython must set
`ECP_NO_RUN=1` first (the executor auto-builds otherwise), and never
import `relay.py` (it runs the poll loop at module level).

## Headless rules

- `system.prompt_handling = PromptHandling.LogSimplePrompts`
  (`_headless_hardening`, runs once per process).
- Never call UI-prompting APIs unattended; set credentials via
  `online.set_default_credentials` before connecting.
- The PLC root directory for file ops is `.` (G-10); `save()` fails
  with "temporary file-name" on a never-saved project (G-11).
