export const meta = {
  name: 'stage-c5-plan-review',
  description: 'Review the Stage C.5 bbox local cut implementation plan across spec, code feasibility, API risk, tests, and portability',
  phases: [
    { title: 'Spec-Plan Alignment', detail: 'Does the plan faithfully implement the Stage C.5 local cut design spec?' },
    { title: 'Existing-Code Feasibility', detail: 'Can the plan land cleanly on the current ansys-agent codebase?' },
    { title: 'PyEDB/PyAEDT API Risk', detail: 'Identify risky or likely-wrong PyEDB/PyAEDT assumptions before implementation.' },
    { title: 'Local Cut Semantics', detail: 'Check bbox/polygon cutout semantics and fail-closed behavior.' },
    { title: 'Port Selection Risk', detail: 'Review uniform-line edge candidate and port creation assumptions.' },
    { title: 'Testing Plan Quality', detail: 'Assess whether planned tests catch the important failures.' },
    { title: 'Production Portability', detail: 'Check portability across local Linux demo and Windows/internal production environments.' },
    { title: 'Synthesize', detail: 'Synthesize findings into a prioritized plan review report.', model: 'gpt-5.5' },
  ],
};

const repo = '/home/zzmjay/code/ansys-agent';
const specPath = `${repo}/docs/superpowers/specs/2026-05-31-stage-c5-local-cut-optimization-cell-design.md`;
const planPath = `${repo}/docs/superpowers/plans/2026-05-31-stage-c5-bbox-local-cut-build.md`;

const commonContext = `
Repository: ${repo}
Spec: ${specPath}
Plan under review: ${planPath}

Relevant current files:
- ${repo}/src/aedt_agent/demo/import_cutout.py
- ${repo}/src/aedt_agent/layout/ports.py
- ${repo}/src/aedt_agent/layout/import_cutout.py
- ${repo}/scripts/run_stage_c5_recorded_build.py
- ${repo}/tests/test_import_cutout_demo.py
- ${repo}/tests/test_stage_c5_recorded_build_runner.py
- ${repo}/tests/test_layout_port_candidates.py

Review the plan, not an already-completed implementation. Flag concrete issues that should be fixed in the plan before coding.
`;

phase('Spec-Plan Alignment');

const specPlanReview = await agent(`
You are reviewing the Stage C.5 bbox local cut implementation plan for ansys-agent.

${commonContext}

Read the spec and the plan carefully.

Evaluate:
1. Does the plan cover every requirement in the spec?
2. Does the plan contradict the spec in any way?
3. Does the plan add scope that is not needed for the first build-only local cut cell?
4. Are the done criteria traceable to the spec?
5. Are any spec requirements too vague in the plan to implement safely?

Output JSON:
{
  "summary": "1-2 sentence assessment",
  "coverage": {
    "full": ["spec requirements fully covered"],
    "partial": [{"requirement": "...", "gap": "..."}],
    "missing": ["spec requirements missing from the plan"]
  },
  "contradictions": [{"spec_says": "...", "plan_says": "...", "severity": "high|medium|low"}],
  "scope_creep": [{"item": "...", "recommendation": "keep|remove|defer"}],
  "acceptance_gaps": ["verification or done criteria gaps"],
  "plan_edits_required": ["specific edits to make before implementation"]
}
`, { label: 'spec-plan-alignment', phase: 'Spec-Plan Alignment', schema: {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    coverage: { type: 'object', properties: {
      full: { type: 'array', items: { type: 'string' } },
      partial: { type: 'array', items: { type: 'object', properties: { requirement: { type: 'string' }, gap: { type: 'string' } } } },
      missing: { type: 'array', items: { type: 'string' } },
    } },
    contradictions: { type: 'array', items: { type: 'object', properties: { spec_says: { type: 'string' }, plan_says: { type: 'string' }, severity: { type: 'string' } } } },
    scope_creep: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, recommendation: { type: 'string' } } } },
    acceptance_gaps: { type: 'array', items: { type: 'string' } },
    plan_edits_required: { type: 'array', items: { type: 'string' } },
  },
}, model: 'gpt-5.4' });

phase('Existing-Code Feasibility');

const codeFeasibilityReview = await agent(`
You are reviewing whether the Stage C.5 plan can be implemented cleanly in the current ansys-agent codebase.

${commonContext}

Read the plan and the listed current files. Also run:
- git -C ${repo} status --short
- git -C ${repo} log --oneline -8
- rg -n "def .*cutout|edb.cutout|ImportCutoutRequest|recorded_hfss_extents|locate_layout_port" ${repo}/src ${repo}/scripts ${repo}/tests

Evaluate:
1. Are planned files and functions placed in sensible modules?
2. Does the plan reference functions, imports, or tests that conflict with existing names?
3. Will the planned helper extraction from scripts/run_stage_c5_recorded_build.py preserve current behavior?
4. Are planned tests compatible with the current test style?
5. Is there any hidden coupling in import_cutout.py that the plan misses?

Output JSON:
{
  "summary": "1-2 sentence assessment",
  "feasible_as_written": true,
  "blocking_issues": [{"issue": "...", "file": "...", "fix": "..."}],
  "non_blocking_risks": [{"risk": "...", "recommendation": "..."}],
  "naming_conflicts": ["..."],
  "test_integration_issues": ["..."],
  "plan_edits_required": ["specific edits to make before implementation"]
}
`, { label: 'existing-code-feasibility', phase: 'Existing-Code Feasibility', schema: {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    feasible_as_written: { type: 'boolean' },
    blocking_issues: { type: 'array', items: { type: 'object', properties: { issue: { type: 'string' }, file: { type: 'string' }, fix: { type: 'string' } } } },
    non_blocking_risks: { type: 'array', items: { type: 'object', properties: { risk: { type: 'string' }, recommendation: { type: 'string' } } } },
    naming_conflicts: { type: 'array', items: { type: 'string' } },
    test_integration_issues: { type: 'array', items: { type: 'string' } },
    plan_edits_required: { type: 'array', items: { type: 'string' } },
  },
}, model: 'gpt-5.4' });

phase('PyEDB/PyAEDT API Risk');

const apiRiskReview = await agent(`
You are reviewing PyEDB and PyAEDT API assumptions in the Stage C.5 plan.

${commonContext}

Read the plan and inspect the installed package when useful. Suggested commands:
- .venv/bin/python - <<'PY'
import inspect
try:
    from pyedb import Edb
    print("Edb", Edb)
    print("has cutout", hasattr(Edb, "cutout"))
except Exception as exc:
    print(type(exc).__name__, exc)
PY
- rg -n "def cutout|custom_extent|extent_type|CreatePortsOnComponentsByNet|CreateEdgePort|Circuit Port|ToggleViaPin" ${repo}/.venv/lib/python3.12/site-packages/pyedb ${repo}/.venv/lib/python3.12/site-packages/ansys || true

Evaluate:
1. Does the plan assume an API signature that may not exist?
2. Are bbox polygon units handled explicitly enough for PyEDB?
3. Does the plan include a verification point for real PyEDB behavior before relying on fake tests?
4. Are Hfss3dLayout save/open/setup expectations realistic?
5. Which API assumptions must be validated with a real small board before implementation proceeds to production use?

Output JSON:
{
  "summary": "1-2 sentence assessment",
  "api_assumptions": [{"assumption": "...", "confidence": "high|medium|low", "evidence": "..."}],
  "likely_breakages": [{"area": "...", "why": "...", "severity": "high|medium|low"}],
  "unit_risks": ["..."],
  "required_spikes": [{"goal": "...", "command_or_file": "..."}],
  "plan_edits_required": ["specific edits to make before implementation"]
}
`, { label: 'pyedb-pyaedt-api-risk', phase: 'PyEDB/PyAEDT API Risk', schema: {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    api_assumptions: { type: 'array', items: { type: 'object', properties: { assumption: { type: 'string' }, confidence: { type: 'string' }, evidence: { type: 'string' } } } },
    likely_breakages: { type: 'array', items: { type: 'object', properties: { area: { type: 'string' }, why: { type: 'string' }, severity: { type: 'string' } } } },
    unit_risks: { type: 'array', items: { type: 'string' } },
    required_spikes: { type: 'array', items: { type: 'object', properties: { goal: { type: 'string' }, command_or_file: { type: 'string' } } } },
    plan_edits_required: { type: 'array', items: { type: 'string' } },
  },
}, model: 'gpt-5.4' });

phase('Local Cut Semantics');

const localCutReview = await agent(`
You are reviewing local cut semantics in the Stage C.5 plan.

${commonContext}

Focus on bbox validation, bbox-to-polygon conversion, local cutout behavior, summary artifacts, and fail-closed rules.

Evaluate:
1. Is bbox validation strict enough for production use?
2. Does bbox-to-polygon define orientation, closure, unit, and coordinate frame clearly enough?
3. Does the plan prevent accidental whole-channel or whole-board cutout fallback?
4. Are progress events and summary artifacts sufficient for debugging failed cutout?
5. Are artifact names consistent and useful for later optimization iterations?

Output JSON:
{
  "summary": "1-2 sentence assessment",
  "semantic_gaps": [{"gap": "...", "impact": "...", "fix": "..."}],
  "fail_closed_assessment": "assessment",
  "artifact_gaps": ["..."],
  "debuggability_gaps": ["..."],
  "plan_edits_required": ["specific edits to make before implementation"]
}
`, { label: 'local-cut-semantics', phase: 'Local Cut Semantics', schema: {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    semantic_gaps: { type: 'array', items: { type: 'object', properties: { gap: { type: 'string' }, impact: { type: 'string' }, fix: { type: 'string' } } } },
    fail_closed_assessment: { type: 'string' },
    artifact_gaps: { type: 'array', items: { type: 'string' } },
    debuggability_gaps: { type: 'array', items: { type: 'string' } },
    plan_edits_required: { type: 'array', items: { type: 'string' } },
  },
}, model: 'gpt-5.4' });

phase('Port Selection Risk');

const portRiskReview = await agent(`
You are reviewing the uniform-line edge port portion of the Stage C.5 plan.

${commonContext}

Read current port-related code and the plan's proposed tests.

Evaluate:
1. Does the planned Primitive test model resemble real EDB/HFSS 3D Layout primitives enough?
2. Is candidate scoring by distance to bbox side sufficient for differential lines and multiple layers?
3. Does the plan handle P/N pairs, reference net, edge orientation, and vertical circuit port direction?
4. Does the plan define what happens when candidates are ambiguous?
5. Which port-creation parts should remain candidate-report-only until real AEDT validation?

Output JSON:
{
  "summary": "1-2 sentence assessment",
  "port_risks": [{"risk": "...", "severity": "high|medium|low", "recommendation": "..."}],
  "candidate_reporting_gaps": ["..."],
  "creation_risks": ["..."],
  "real_aedt_validation_needed": ["..."],
  "plan_edits_required": ["specific edits to make before implementation"]
}
`, { label: 'port-selection-risk', phase: 'Port Selection Risk', schema: {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    port_risks: { type: 'array', items: { type: 'object', properties: { risk: { type: 'string' }, severity: { type: 'string' }, recommendation: { type: 'string' } } } },
    candidate_reporting_gaps: { type: 'array', items: { type: 'string' } },
    creation_risks: { type: 'array', items: { type: 'string' } },
    real_aedt_validation_needed: { type: 'array', items: { type: 'string' } },
    plan_edits_required: { type: 'array', items: { type: 'string' } },
  },
}, model: 'gpt-5.4' });

phase('Testing Plan Quality');

const testingReview = await agent(`
You are reviewing the test strategy in the Stage C.5 plan.

${commonContext}

Evaluate:
1. Do the tests fail for the right reason before implementation?
2. Do fake tests overfit to fake objects?
3. Are there missing negative tests for invalid bbox, ambiguous ports, API failures, and no fallback?
4. Does the plan verify existing Stage C.5 recorded build behavior is not broken?
5. Are verification commands focused enough and complete enough?

Output JSON:
{
  "summary": "1-2 sentence assessment",
  "strong_tests": ["..."],
  "missing_tests": [{"area": "...", "test_to_add": "...", "priority": "high|medium|low"}],
  "overfit_risks": ["..."],
  "verification_gaps": ["..."],
  "plan_edits_required": ["specific edits to make before implementation"]
}
`, { label: 'testing-plan-quality', phase: 'Testing Plan Quality', schema: {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    strong_tests: { type: 'array', items: { type: 'string' } },
    missing_tests: { type: 'array', items: { type: 'object', properties: { area: { type: 'string' }, test_to_add: { type: 'string' }, priority: { type: 'string' } } } },
    overfit_risks: { type: 'array', items: { type: 'string' } },
    verification_gaps: { type: 'array', items: { type: 'string' } },
    plan_edits_required: { type: 'array', items: { type: 'string' } },
  },
}, model: 'gpt-5.4' });

phase('Production Portability');

const portabilityReview = await agent(`
You are reviewing whether the Stage C.5 plan is portable from the local Linux demo machine to a Windows/internal production environment.

${commonContext}

Focus on path handling, AEDT version configuration, Cadence launcher assumptions, graphical/non-graphical behavior, secrets, large board runtime, and artifact locations.

Evaluate:
1. Does the plan avoid hard-coded local AEDT installation paths?
2. Are CLI parameters sufficient for Windows/internal production use?
3. Does it avoid committing secrets or machine-specific runtime artifacts?
4. Does it keep model-build-only behavior by default for large boards?
5. Are logs and failure summaries sufficient for production debugging?

Output JSON:
{
  "summary": "1-2 sentence assessment",
  "portability_issues": [{"issue": "...", "severity": "high|medium|low", "fix": "..."}],
  "path_config_assessment": "...",
  "runtime_risks": ["..."],
  "artifact_and_secret_risks": ["..."],
  "plan_edits_required": ["specific edits to make before implementation"]
}
`, { label: 'production-portability', phase: 'Production Portability', schema: {
  type: 'object',
  properties: {
    summary: { type: 'string' },
    portability_issues: { type: 'array', items: { type: 'object', properties: { issue: { type: 'string' }, severity: { type: 'string' }, fix: { type: 'string' } } } },
    path_config_assessment: { type: 'string' },
    runtime_risks: { type: 'array', items: { type: 'string' } },
    artifact_and_secret_risks: { type: 'array', items: { type: 'string' } },
    plan_edits_required: { type: 'array', items: { type: 'string' } },
  },
}, model: 'gpt-5.4' });

phase('Synthesize');

const allFindings = {
  spec_plan_alignment: specPlanReview,
  existing_code_feasibility: codeFeasibilityReview,
  pyedb_pyaedt_api_risk: apiRiskReview,
  local_cut_semantics: localCutReview,
  port_selection_risk: portRiskReview,
  testing_plan_quality: testingReview,
  production_portability: portabilityReview,
};

const report = await agent(`
You are the lead reviewer synthesizing the Stage C.5 bbox local cut plan review for ansys-agent.

Raw findings:
${JSON.stringify(allFindings, null, 2)}

Produce a concise consolidated review. Deduplicate overlapping findings. Prioritize issues that should change the plan before implementation.

Output JSON:
{
  "title": "Stage C.5 Bbox Local Cut Plan Review",
  "date": "2026-05-31",
  "executive_summary": "2-3 paragraphs",
  "overall_grade": "A|B|C|D|F",
  "confidence": "high|medium|low",
  "must_fix_plan_changes": [{"id": "P01", "title": "...", "why": "...", "recommended_plan_edit": "...", "severity": "high|medium"}],
  "implementation_watchpoints": [{"id": "W01", "area": "...", "risk": "...", "verification": "..."}],
  "deferred_items": [{"item": "...", "reason": "..."}],
  "positive_highlights": ["..."],
  "dimension_summaries": {
    "spec_plan_alignment": "...",
    "existing_code_feasibility": "...",
    "pyedb_pyaedt_api_risk": "...",
    "local_cut_semantics": "...",
    "port_selection_risk": "...",
    "testing_plan_quality": "...",
    "production_portability": "..."
  }
}
`, { label: 'synthesize-stage-c5-plan-review', phase: 'Synthesize', schema: {
  type: 'object',
  properties: {
    title: { type: 'string' },
    date: { type: 'string' },
    executive_summary: { type: 'string' },
    overall_grade: { type: 'string' },
    confidence: { type: 'string' },
    must_fix_plan_changes: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, title: { type: 'string' }, why: { type: 'string' }, recommended_plan_edit: { type: 'string' }, severity: { type: 'string' } } } },
    implementation_watchpoints: { type: 'array', items: { type: 'object', properties: { id: { type: 'string' }, area: { type: 'string' }, risk: { type: 'string' }, verification: { type: 'string' } } } },
    deferred_items: { type: 'array', items: { type: 'object', properties: { item: { type: 'string' }, reason: { type: 'string' } } } },
    positive_highlights: { type: 'array', items: { type: 'string' } },
    dimension_summaries: { type: 'object', properties: {
      spec_plan_alignment: { type: 'string' },
      existing_code_feasibility: { type: 'string' },
      pyedb_pyaedt_api_risk: { type: 'string' },
      local_cut_semantics: { type: 'string' },
      port_selection_risk: { type: 'string' },
      testing_plan_quality: { type: 'string' },
      production_portability: { type: 'string' },
    } },
  },
}, model: 'gpt-5.5' });

return report;
