export const name = 'ansys-reviewed-tools'
export const inject = ['tools']

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
  // Execution-level boundary: even a tool hidden from the catalog must not be
  // callable by name through the shared profile's registered tools.
  const disposeGuard = ctx.tools.guard((execution) => (
    isReviewedTool(execution.name)
      ? undefined
      : `ansys-reviewed preset denies tool: ${execution.name}`
  ))
  ctx.effect?.(() => disposeGuard())

  ctx.on('system-prompt/assemble', async (_assembly, _context, next) => {
    const assembled = await next()
    return {
      ...assembled,
      tools: assembled.tools.filter((tool) =>
        isReviewedTool(tool.name)),
    }
  })
}

function isReviewedTool(name) {
  return REVIEWED_TOOLS.has(name)
    || REVIEWED_PREFIXES.some((prefix) => name.startsWith(prefix))
}
