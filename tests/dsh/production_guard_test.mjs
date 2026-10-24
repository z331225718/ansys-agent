import assert from 'node:assert/strict'
const plugin = new URL('../../integrations/dsh/bundles/ansys-engineering/production-guard.mjs', import.meta.url)
const mod = await import(plugin.href)
assert.deepEqual(mod.inject, ['tools'])
let guard
const ctx = { tools: { guard: (fn) => { guard = fn; return () => {} } }, effect: () => {} }
mod.apply(ctx)
assert.ok(guard)
for (const denied of [
  'mcp__ansys_assistant__preview_ansys_workflow_start',
  'mcp__ansys_assistant__apply_ansys_workflow_start',
  'mcp__ansys_assistant__preview_ansys_workflow_advance',
  'mcp__ansys_assistant__apply_ansys_workflow_advance',
]) {
  assert.match(guard({ name: denied }), /controlled local CLI\/dashboard/)
}
assert.equal(guard({ name: 'mcp__ansys_assistant__preview_live_hfss_geometry_create' }), undefined)
assert.equal(guard({ name: 'mcp__aedt__run_python_code' }), undefined)
console.log('production guard passed')
