export const name = 'ansys-production-guard'
export const inject = ['tools']

const DENIED = new Set([
  'mcp__ansys_assistant__preview_ansys_workflow_start',
  'mcp__ansys_assistant__apply_ansys_workflow_start',
  'mcp__ansys_assistant__preview_ansys_workflow_advance',
  'mcp__ansys_assistant__apply_ansys_workflow_advance',
])

/**
 * Production Mission lifecycle stays in the controlled local CLI/dashboard.
 * Geometry preview/apply remains available; DSH may not start or advance a
 * reviewed BRD graph by itself.
 */
export function apply(ctx) {
  const dispose = ctx.tools.guard((execution) => (
    DENIED.has(execution.name)
      ? 'production Mission start/advance/approval must use the controlled local CLI/dashboard'
      : undefined
  ))
  ctx.effect?.(() => dispose())
}
