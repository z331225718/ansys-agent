---
name: ansys-capability-promoter
description: Classify a server-owned, sealed, verified AEDT exploration trace and generate a review-only Harness, Skill, or Workflow candidate. Use when an unknown PyAEDT/PyEDB operation has succeeded with readback and should be considered for reuse, without changing or hot-registering the running assistant.
---

# Ansys Capability Promoter

Promote evidence, not conversation text. Accept only a trace ID returned by the assistant's capability trace store.

## Procedure

1. Read [trace-contract.md](references/trace-contract.md) and reject unsealed, non-verified, copied, or user-authored trace JSON.
2. Read [classification-rules.md](references/classification-rules.md). Keep `auto` unless a reviewer explicitly chooses another target kind.
3. Generate a candidate from the repository root:

```powershell
.venv\Scripts\python.exe -m aedt_agent.capability_learning promote --trace-id <trace-id> --target-kind auto
```

4. Inspect `.aedt-agent/capability-candidates/<candidate-id>/promotion-report.md`, `candidate.patch`, `candidate.json`, and `generated/`.
5. Confirm that project names, design names, object paths, local source paths, credentials, and approval tokens are absent from every candidate artifact.
6. Read [acceptance-gates.md](references/acceptance-gates.md). Leave the candidate disabled until a human reviews its implementation and tests in a later change.

## Hard Rules

- Never pass arbitrary JSON to the promoter; use a server-owned trace ID.
- Never apply `candidate.patch`, register a tool, edit a Workflow, commit, or approve a candidate automatically.
- Never turn API Memory evidence into permission to execute raw Python, shell, or COM code.
- Keep known operations in typed Harness APIs. Use a Skill for judgment and composition, and a Workflow for loops, branches, budgets, recovery, or multiple approvals.
- Treat one verified trace as candidate evidence, not production readiness.
