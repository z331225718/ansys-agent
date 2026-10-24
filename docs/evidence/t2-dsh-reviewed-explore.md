> **Superseded by ADR-0003:** reviewed/explore were replaced by one `ansys-engineering` DSH mode on 2026-10-24.

# T2 DSH reviewed / explore evidence

- Date: 2026-10-24
- Branch: feature/reviewed-brd-dsh-loop
- Scope: DSH presets, reviewed MCP allowlist, idempotent installer

## Reviewed MCP surface

Real FastMCP server with `AEDT_AGENT_MCP_PROFILE=reviewed` registers exactly:

- list_ansys_capabilities_v2
- list_ansys_workflows
- inspect_ansys_workflow
- get_ansys_workflow_status

No preview/apply, geometry/material/via mutation, exploration execute, capability promotion, or official PyAEDT-MCP.

## Client-side catalog filter

`integrations/dsh/agent-presets/ansys-reviewed/reviewed-tools.mjs` filters the assembled DSH catalog to:

- the four reviewed assistant tools (server name `ansys_assistant_reviewed`);
- `mcp__ansys_api_memory__*`;
- `ask_user_question`.

The filter always narrows; it never falls back to the full catalog.

The plugin also registers an execution-level `ctx.tools.guard`; a black-box Node test proves hidden `pwsh`, `read`, `mcp__aedt__run_python_code`, full `mcp__ansys_assistant__*` preview and `subagent` calls are denied, not merely hidden.

## Installer

`scripts/dsh/install_ansys_dsh.py check|apply --repo-root ... --dsh-profile ... --python ... --workflow-db ...`

Verified in temporary profiles:

- check is read-only;
- first apply adds one managed marker block;
- second apply does not duplicate presets or MCP rows;
- unrelated `!!js` content is preserved;
- unmarked legacy `preset-ansys-engineering` fails before any write;
- generated block parses as YAML after replacing `!!js` placeholders;
- conflict detection tolerates extra whitespace, quotes, and line comments around legacy ids;
- reviewed block has no official AEDT MCP, shell, filesystem, PTC, preview/apply;
- `--workflow-db` is propagated to the reviewed MCP process;
- a manifest binds the installed reviewed DB canonical path and later check/apply fails if it changes;
- unchanged apply is a no-op: no backup, no rewrite.
- the reviewed catalog filter plugin is referenced by an installer-generated absolute path, avoiding inline-preset relative-path ambiguity.

## Real profile check (no apply)

`check` against the real desktop profile:

- result: failed;
- reason: unmarked legacy `preset-ansys-engineering` exists;
- no file was modified.

This is intentional: unmarked user/legacy entries are never overwritten. Migration requires a human decision to rename/remove the legacy preset, then re-run apply.

## Real-profile activation (2026-10-24)

User explicitly confirmed migration. Before any write:

- backed up `cordis.patch.yml`;
- archived `profiles/desktop/presets/ansys-engineering.patch.yml` as `.legacy-*`;
- removed the single unmarked `preset-ansys-engineering` insert block;
- initialized `D:\aedt-agent-runs\reviewed-loop\missions.db` with the mission schema;
- installer check passed, then apply succeeded and created its own backup;
- second apply returned `applied=false, unchanged=true`;
- the full profile parses as YAML after replacing `!!js` placeholders and contains exactly:
  `mcp-ansys-assistant-reviewed`, `preset-ansys-reviewed`, `preset-ansys-explore`;
- the managed block parses with `dsh --profile headless --patch ... --dump-config`.

DSH was not restarted by the installer.

## 2026-10-24 activation failure and fix

The first DSH restart failed because the reviewed catalog plugin used `ctx.tools` without declaring the Cordis dependency. The DSH loader reported:

~~~text
cannot get property "tools" without inject
~~~

Fix: `export const inject = ['tools']` in `reviewed-tools.mjs`. The plugin was reloaded through the real DSH headless loader and now only stops at the expected missing-credential check, with no plugin activation error.

## Not yet verified

- GUI selector showing both reviewed and explore after restart (profile is installed; DSH restart is still required).
- Real preset activation and loader behavior for the absolute plugin path (requires an explicit real-profile apply; no activation was claimed).
- Real reviewed status read against the production Mission DB (requires real graph run).

## Final regression

- 1464 passed, 31 skipped (excluding tests/test_offline_release_scripts.py).
