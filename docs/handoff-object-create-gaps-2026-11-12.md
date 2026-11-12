# Handoff: ecockpit-mcp — object_create / push-sitting findings (2026-11-12)

Context: s4 refactor campaign push sitting (98 SOT→GUI changes via
`tools/ecockpit_st_load.py push --sot-src src --st . --confirm`, project
`s4_3.13.2.ecp`). Five loader failures + two live compile findings were hit;
two ecockpit-mcp patches were APPLIED same-day (below), the rest is open work.
Evidence: `s4/main/tmp/push-sitting.log`, `s4/main/tmp/push-fix.log`,
`tmp/fragments*.json`, session transcript; s4 side commit `0d08ecd`
(enum member rename) and `7dc061c` (loader patches + v3.13.2 bump).

## APPLIED (2026-11-12, live-verified — REVIEW REQUESTED)

### 1. executor.py `cmd_object_create`: `dut_kind` arg (was: DutType.Structure hardcoded)
- `kind=dut` always created **Structure**-kind DUTs. An ENUM DUT created
  structure-kind with enum text compiles as struct-parse garbage:
  C6 `';' expected instead of ','` + C368/C26/C135 cascade (19 errors on
  s4 `eEntitySubType`; the compile says `for an enumeration` in C368 while
  failing to parse the member list — kind/property mismatch).
- Fix: `dut_kind` arg (structure|enumeration|union|alias) → DutType member
  probed via `hasattr` with the known-members list in the error text.
- `server.py`: `dut_kind: str = ""` param added to `ecockpit_object_create`
  and forwarded; **daemon restarted** (editable venv confirmed via
  `__editable__.ecockpit_mcp-0.1.0.pth`).
- Live-verified: enum DUT via `dut_kind=enumeration` compiles clean
  (zChkEnum probe: zero zChkEnum-specific errors in a full compile).

### 2. executor.py `cmd_object_create` docstring: `return_type` for
### pou_type=function is REQUIRED (loader was omitting it)
- `create_pou(name, type=Function, ...)` **without** `return_type` throws
  .NET `ValueError: Specified argument was out of the range of valid
  values` (s4 `fMqttPublishResult`/`fFindEntity`). With `return_type="BOOL"`
  the same create succeeds (proven twice same day).
- Loader side fix (s4 repo, `ecockpit_st_load.py build_create_args`):
  parses the return type from the SOT declaration's first line
  (`FUNCTION f : BOOL`) and passes it. REVIEW: consider a clear Python-side
  error in the executor when pou_type=function arrives with empty
  return_type (fail loud instead of the opaque .NET range error).
  **DONE 2026-11-12**: the executor now refuses `pou_type=function`
  with an empty/whitespace return_type up front (clear message, unit
  test `test_object_create_function_requires_return_type`).

## OPEN (to implement / research)

### 3. METHOD creation under a PROGRAM parent is impossible via object_create
- **RESOLVED 2026-11-12 — see the VERDICT in
  `handoff-program-method-gap-2026-11-12.md`: creation was never
  GUI-only; `found[0]` resolved to a same-named non-POU structural
  node (G-32). Fixed via member-container probing; live-verified on a
  program parent (create + textual write + build 0E/0W).**
- `object_create kind=method parent=<PROGRAM>` →
  `AttributeError: 'ScriptObject' object has no attribute 'create_method'`
  (executor.py ~5010: container = the found program ScriptObject; FB
  parents expose a container WITH create_method, PROGRAM parents do not).
- Programs DO own methods in e!COCKPIT (s4 GUI is full of them) — the
  scripting path just doesn't expose creation. Research the CHM/API for the
  program-method creation call (probe `dir()` on a program ScriptObject;
  SCRIPTENGINE_EXPLORATION.md has the probe recipe).
- Workaround used (owner manual, once per method): GUI Add Object → Method,
  then `textual_write` decl+impl — writes to program-methods work fine.
- Repro: any `object_create kind=method parent=<PROGRAM>`.

### 4. `tree op=move` of a METHOD into a PROGRAM parent: moved:true, object LOST
- **ROOT-CAUSED + GUARDED 2026-11-12 — see the VERDICT in
  `handoff-program-method-gap-2026-11-12.md` (G-32): the loss happens
  when the move TARGET resolves to a same-named non-POU structural
  node (find returns it first); moving into it silently destroys the
  object (engine-level, reproduced 3x incl. folder + real FB target).
  Moves into the real POUs work end-to-end (method -> program POU
  verified live). Destructive tree ops now refuse ambiguous names;
  move re-finds by bare name + guid ("SILENT LOSS GUARD").**
- Created a method under an FB, `tree op=move new_parent=<PROGRAM>` returned
  `moved: true` — then the object is GONE from the project entirely
  (find 0, no parent candidate, no error). **Silent data loss** — at minimum
  the move op must re-find the object after the move and fail loudly if
  absent; ideally refuse method moves into PROGRAM parents until the root
  cause is known (candidate tie-in with finding 3: if programs can't own
  script-created methods, move should refuse).
- Repro: create method under any FB → `tree op=move new_parent=<PROGRAM>`.

### 5. Loader (s4 repo, applied same day — mirror here when vendoring):
`ecockpit_st_load.py`:
- `build_create_args`: `return_type` parsed from the SOT declaration for
  `pou_type=function`; `dut_kind` derived from the DUT declaration
  (`: (` → enumeration, UNION → union, default structure) — both forwarded
  to object_create. NOTE: server.py must be the patched build (dut_kind
  param) — a pre-patch daemon silently DROPS the arg (unknown params are
  ignored server-side, no error!) and the executor defaults to Structure.
- push write loop: whitespace-only declarations are SKIPPED
  (`if sot_decl.strip():`) — ACTION objects have no declaration part and
  the decl write used to fail `has no textual declaration`, starving the
  impl write (failure + skipped impl = the action never updates).
- GOTCHA for both: python file writes WITHOUT `encoding=` on Windows are
  cp1252 — a composed JSON snapshot written that way got lone-0x97
  em-dashes and broke the trust-runtime harness UTF-8 decode. Always
  `open(..., encoding='utf-8')`.

## S4-side compile finding (not ecockpit-mcp, recorded for the ledger)
- Enum member named `log` breaks the live e!COCKPIT parse
  (C6/C26/C135 — IEC LOG function name collision). The trust dialect
  ACCEPTS `log` (gates green) — dialect divergence, renamed
  `log_select` (s4 commit `0d08ecd`). Upstream-candidate note for
  trust-platform: reserved-word parity with the live compiler.

## Verification state after the sitting
`ecockpit_build(skip_import)`: **0E/0W** · check: **0 drifted/0 missing** ·
project **saved** as `s4_3.13.2.ecp` (post-recovery session; the previous
session died on System.OutOfMemoryException in the MessagesView UI path
after ~150 scripting mutations + several builds in one long GUI session —
see s4/main AGENTS.md notes when written).
