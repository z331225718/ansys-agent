# Classification Rules

Choose the smallest durable layer that owns the behavior:

| Signal | Candidate kind |
|---|---|
| One bounded deterministic read or reversible mutation with a typed contract | Harness |
| Read-only engineering judgment or composition of existing Harness tools | Skill |
| Loop, branch, retry, budget, recovery, handoff, or multiple approval points | Workflow |

`auto` applies these rules conservatively. An explicit kind is only a reviewer request; it does not bypass trace validation, parameterization, tests, or human approval.

Do not promote an operation merely because it worked once. Keep it observed when its behavior depends on a specific project name, design name, object ID, local path, undocumented COM handle, or unstable private API. A candidate may proceed only after those values become explicit request parameters and the implementation remains inside typed Harness APIs.
