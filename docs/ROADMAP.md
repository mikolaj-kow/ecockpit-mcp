# Outlook: the state-of-the-art ecockpit-mcp package

Where the packaging and the tool are headed, from the current state
(src-layout package, lint/test/build CI, wheel + console entry point) to a
mature, corporate-adoptable distribution.

## Positioning

e!COCKPIT is discontinued by WAGO, but a large installed base of e!COCKPIT
projects and PLCs remains in service. The state-of-the-art goal is to be
**best-in-class maintenance tooling for existing e!COCKPIT installations** —
the tool that makes an aging platform scriptable, diagnosable, and
AI-drivable for as long as those installations live. CODESYS V3.5 support
is an explicit non-goal (another MCP project targets that platform).

## Packaging (distribution-grade)

- **src-layout**: done — `src/ecockpit_mcp/` ships as a real package
  (`server.py`, `cmd_server.py`, `executor.py` stay siblings so their
  file-adjacency contract holds in site-packages too). Remaining piece:
  an `ecockpit-mcp install-scripts <dir>` command that writes
  `cmd_server.py` + `executor.py` out to an arming directory of the
  user's choice, so the wheel alone is enough to arm e!COCKPIT.
- **Version single-sourcing** (`__version__` read by pyproject or
  setuptools-scm), **semver discipline**, and a **CHANGELOG**
  (Keep a Changelog format).
- **PyPI publication** via GitHub Actions trusted publishing (OIDC, no
  long-lived token); `pipx install ecockpit-mcp` as the documented install
  path; tag-triggered release workflow building sdist+wheel and attaching
  them to a GitHub release.
- **Wheel hygiene**: license/notice metadata (done in this PR),
  `check-manifest`/`twine check` in CI, provenance attestations on publish.

## Quality gates

- **ruff** for lint + format (replaces the flake8 pair; the strict
  E9/F63/F7/F82 gate keeps its role as the fast fail-fast step).
- **Typing**: mypy or pyright clean on the CPython half (the MCP server);
  the ScriptEngine scripts stay deliberately dynamic but keep their guarded
  stand-in blocks so static tools never see undefined names.
- **A ScriptEngine fake**: in-process simulators of `system`, `Severity`,
  `e_projects`, `online`, and the reporters, so the executor's command
  logic (dispatch, summary/harvest, error enrichment) gets real unit tests
  without e!COCKPIT — today only the host half is tested.
- **Coverage gate** on the host half; **pre-commit** hooks running the
  same gates locally.

## MCP-protocol maturity

- **Complete tool annotations** (read-only vs destructive, idempotency) so
  editors can render safe/unsafe calls correctly; the confirm-gate policy
  already in the tools becomes machine-readable metadata, not just prose.
- **Resources**: project tree, current build/messages state exposed as MCP
  resources instead of poll-only tools.
- **Prompts**: canned high-value flows ("diagnose failed build from the
  harvest + e!COCKPIT log", "prepare a release snapshot").
- **Progress notifications** for long operations — today a build blocks a
  single HTTP request for up to 900 s; the SOTA UX streams progress and
  stays responsive.
- **Testable against the MCP spec**: conformance checks against the
  `mcp` reference client in CI.

## Docs

- mkdocs(-material) site: architecture diagram, `.env` reference,
  troubleshooting cookbook; the **tool reference generated from the
  server's tool registry** so docs can't drift from the schema.
- A short security/threat-model page: bearer token, loopback default,
  confirm-gated destructive ops, no credentials in logs — the page a
  corporate reviewer asks for first.

## What stays out of scope

Re-creating e!COCKPIT functionality, CODESYS V3.5 support (another MCP
project targets that platform), and any affiliation with WAGO (see NOTICE)
are non-goals.
