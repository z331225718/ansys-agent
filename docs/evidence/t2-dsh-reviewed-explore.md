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

## Installer

`scripts/dsh/install_ansys_dsh.py check|apply --repo-root ... --dsh-profile ... --python ... --workflow-db ...`

Verified in temporary profiles:

- check is read-only;
- first apply adds one managed marker block;
- second apply does not duplicate presets or MCP rows;
- unrelated `!!js` content is preserved;
- unmarked legacy `preset-ansys-engineering` fails before any write;
- generated block parses as YAML after replacing `!!js` placeholders;
- reviewed block has no official AEDT MCP, shell, filesystem, PTC, preview/apply;
- `--workflow-db` is propagated to the reviewed MCP process.
- the reviewed catalog filter plugin is referenced by an installer-generated absolute path, avoiding inline-preset relative-path ambiguity.

## Real profile check (no apply)

`check` against the real desktop profile:

- result: failed;
- reason: unmarked legacy `preset-ansys-engineering` exists;
- no file was modified.

This is intentional: unmarked user/legacy entries are never overwritten. Migration requires a human decision to rename/remove the legacy preset, then re-run apply.

## Not yet verified

- GUI selector showing both reviewed and explore after restart (requires real-profile apply and DSH restart).
- Real reviewed status read against the production Mission DB (requires real graph run).
