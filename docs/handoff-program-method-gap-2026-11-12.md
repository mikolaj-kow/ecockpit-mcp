# Handoff: program-method creation + tree move data loss (2026-11-12)

Follow-up on `handoff-object-create-gaps-2026-11-12.md` findings **#3** and
**#4** (the two open object_create gaps; the same-day hotfixes — `dut_kind`,
`return_type` doc — landed in commit `8e2dfef`). Scope: ONE exploratory
session to root-cause both, because they are the same underlying question:
**can a PROGRAM own a script-visible method?**

## Finding A (handoff #3): `object_create kind=method` under a PROGRAM parent

- `object_create kind=method parent=<PROGRAM>` →
  `AttributeError: 'ScriptObject' object has no attribute 'create_method'`
  (executor.py, the kind=method branch; container = the found program
  ScriptObject).
- FB parent: `_iecc_container` finds the FB and it EXPOSES `create_method`
  → works.
- PROGRAM parent: the found ScriptObject has NO `create_method`.
- GUI: programs DO own methods (the s4 project is full of them);
  `textual_write` to EXISTING program-methods works fine — only creation
  is missing from the scripting surface.
- Doc evidence: `SCRIPTENGINE_API.md` "ST authoring" documents
  `create_method/create_property/create_action/create_transition` "on
  POUs". Authoritative API surface = the ScriptLib stubs
  `...\CODESYS\ScriptLib\Stubs\scriptengine\*.pyi` (they correct the CHM
  in several places).

## Finding B (handoff #4): `tree op=move` of a METHOD into a PROGRAM parent

- Method created under an FB, `tree op=move new_parent=<PROGRAM>` returned
  `moved: true` — then the object is GONE from the project entirely
  (find 0, no parent candidate, NO error). **SILENT DATA LOSS.**
- Suspicion: the move accepts the target and the engine relocates the
  method into a place the script API cannot address (same root cause as A
  — programs cannot own script-created methods), or the move is
  half-applied. Distinguishing "physically moved but unaddressable" vs
  "deleted" is step 1 of the B investigation.

## Exploration plan (in order)

1. **STUBS FIRST** (cheap, offline): grep the ScriptLib stubs
   (`...\CODESYS\ScriptLib\Stubs\scriptengine\*.pyi`) for which class
   declares `create_method`; note the declaring interface and whether a
   program-typed class inherits it. Probe recipes: `SCRIPTENGINE_EXPLORATION.md`.
   Nuance: `getattr`/`dir` are unusable for the TREE MARKERS on tree
   objects (see SCRIPTENGINE_API.md gotcha), but the `create_*` container
   surface is a normal Python object — verify which one `dir()` actually
   enumerates before trusting its output.
2. **LIVE `dir()` probe**: arm the relay (FILE → Scripting → Execute
   script file → `relay.py`), `dir()` on a program ScriptObject vs an FB
   ScriptObject; diff the member lists.
3. If programs expose no creation path: probe the APPLICATION container
   for a create-method-with-parent overload (create_pou-style parent
   parameter), and the CHM for `ScriptIecLanguageObjectContainer` members
   missing from the stubs.
4. **`tree op=move` root cause**: reproduce with a THROWAWAY project —
   **NEVER the s4 project**, the loss is real. After `moved:true`, walk
   `get_children(recursive)` from the project root and export to XML to
   see where the method physically landed. This also answers A: if the
   method lives under the program but `find()` cannot see it, the bug is
   in addressing, not creation.
5. Whatever the outcome of A: **min fix for B** (executor.py, `tree
   op=move`) — re-find the object by name after the move; if absent →
   `RuntimeError` (fail loud, never silent loss). Optional until A is
   solved: refuse method → PROGRAM moves outright.

## Acceptance

- A: `object_create kind=method parent=<PROGRAM>` works and compiles
  clean, OR the docs record "impossible via scripting, GUI-only" AND the
  executor raises a clear error instead of the bare AttributeError.
- B: move either works end-to-end (object findable after the move) or
  refuses loudly; unit test covers the re-find guard.
- This handoff updated with the verdict; the s4 project untouched during
  move repros.

## Evidence pointers

- `s4/main/tmp/push-sitting.log`, `s4/main/tmp/push-fix.log`,
  `tmp/fragments*.json` — the original session (2026-11-12).
- `docs/handoff-object-create-gaps-2026-11-12.md` — findings #3/#4
  original text + the applied hotfixes.
- `docs/SCRIPTENGINE_EXPLORATION.md` — live probe recipes (relay arming,
  dir() probes).
- `docs/SCRIPTENGINE_API.md` — "ST authoring" section, tree-marker gotcha.
- `docs/SCRIPTENGINE_NOT_EXPOSED.md` — ledger format for "not exposed"
  verdicts (if A ends GUI-only, add an entry there).
