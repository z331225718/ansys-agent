export const name = 'ansys-reviewed-tools'

const REVIEWED_TOOLS = new Set([
  'ask_user_question',
  'mcp__ansys_assistant_reviewed__list_ansys_capabilities_v2',
  'mcp__ansys_assistant_reviewed__list_ansys_workflows',
  'mcp__ansys_assistant_reviewed__inspect_ansys_workflow',
  'mcp__ansys_assistant_reviewed__get_ansys_workflow_status',
])
const REVIEWED_PREFIXES = ['mcp__ansys_api_memory__']

/**
 * Fail-closed catalog restriction for the reviewed preset.
 *
 * The MCP server also enforces this allowlist, but this filter is the DSH
 * client-side boundary: even a globally registered full ansys-assistant or
 * official PyAEDT-MCP server must not appear in the reviewed model catalog.
 */
export function apply(ctx) {
  ctx.on('system-prompt/assemble', async (_assembly, _context, next) => {
    const assembled = await next()
    return {
      ...assembled,
      tools: assembled.tools.filter((tool) =>
        REVIEWED_TOOLS.has(tool.name)
        || REVIEWED_PREFIXES.some((prefix) => tool.name.startsWith(prefix))),
    }
  })
}
