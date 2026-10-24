import assert from 'node:assert/strict'
const pluginUrl = new URL('../../integrations/dsh/agent-presets/ansys-reviewed/reviewed-tools.mjs', import.meta.url)
const mod = await import(pluginUrl.href)
let guard
let assembler
const ctx = {
  tools: { guard: (fn) => { guard = fn; return () => {} } },
  effect: () => {},
  on: (name, fn) => { if (name === 'system-prompt/assemble') assembler = fn },
}
mod.apply(ctx)
assert.ok(guard, 'execution guard must be registered')
assert.equal(guard({ name: 'mcp__ansys_assistant_reviewed__list_ansys_workflows' }), undefined)
for (const denied of [
  'pwsh',
  'read',
  'mcp__aedt__run_python_code',
  'mcp__ansys_assistant__preview_live_hfss_geometry_create',
  'subagent',
]) {
  assert.match(guard({ name: denied }), /denies tool/)
}
const assembled = {
  tools: [
    { name: 'pwsh' },
    { name: 'mcp__ansys_api_memory__search_ansys_api' },
    { name: 'mcp__ansys_assistant_reviewed__get_ansys_workflow_status' },
  ],
}
const filtered = await assembler({}, {}, async () => assembled)
assert.deepEqual(
  filtered.tools.map((tool) => tool.name),
  ['mcp__ansys_api_memory__search_ansys_api', 'mcp__ansys_assistant_reviewed__get_ansys_workflow_status'],
)
console.log('reviewed guard and catalog filter passed')
