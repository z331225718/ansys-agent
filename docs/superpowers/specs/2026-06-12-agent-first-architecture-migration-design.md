# Agent-First Architecture Migration Design

## Status

- Date: 2026-06-12
- Status: Approved for implementation planning
- Scope: Package architecture migration and runtime boundaries
- First production scenario: BRD local-cut via optimization

## Goal

Restructure `ansys-agent` so that the default product is a persistent,
goal-driven Agent runtime while preserving the existing Stage A/B/C application
as a runnable `v0` system.

The migration must retain the tested AEDT domain capabilities already present in
the repository. It must not turn a package move into a broad rewrite of
Workflow, Node, PyAEDT, PyEDB, layout, validation, or reporting behavior.

## Product Definition

The new default product is responsible for the complete mission lifecycle:

```text
User request
    -> mission creation
    -> planning
    -> worker dispatch
    -> AEDT execution
    -> evidence collection
    -> deterministic evaluation
    -> retry, approval, replan, rollback, or completion
    -> final engineering delivery
```

An execution is not considered an Agent mission merely because an LLM generated
a Workflow. A mission must retain its goal and state across multiple jobs, use
intermediate evidence to choose the next action, survive a process restart at a
completed job boundary, and terminate with an explicit engineering outcome.

## Migration Strategy

Use the approved **legacy application archive with shared domain capabilities**
strategy.

### Move into `v0`

The existing application-level products move under `aedt_agent.v0`:

- `demo`
- `benchmark`
- `chat`
- `evolution`
- the current benchmark-oriented CLI

These modules represent the previous product experience, planning entry points,
benchmark harnesses, and demo-specific orchestration. They remain runnable and
tested as the `v0` product.

### Keep shared initially

The following packages remain at their existing import paths during the first
migration:

- `workflow`
- `nodes`
- `layout`
- `validation`
- `mcp`
- `knowledge`
- `reporting`

They contain tested domain behavior used by both the legacy application and the
new Agent runtime. Moving them immediately would create high import churn
without improving Agent behavior.

New Agent code may depend on these shared packages through explicit adapters.
The shared packages must not depend on `aedt_agent.agent`.

### Introduce new top-level boundaries

```text
src/aedt_agent/
├── agent/
│   ├── mission/
│   ├── orchestrator/
│   ├── planning/
│   ├── workers/
│   ├── evaluation/
│   ├── policies/
│   └── approvals/
├── domain/
├── infrastructure/
├── v0/
│   ├── benchmark/
│   ├── chat/
│   ├── demo/
│   ├── evolution/
│   └── cli.py
├── workflow/
├── nodes/
├── layout/
├── validation/
├── mcp/
├── knowledge/
└── reporting/
```

`domain/` and `infrastructure/` are intentional target boundaries, not empty
copies of the existing code. Code moves into them only when the Agent vertical
slice establishes a stable interface and a concrete ownership reason.

## Package Responsibilities

### `aedt_agent.agent`

Owns product-level Agent behavior.

It may decide:

- what job should run next;
- whether evidence satisfies the mission;
- whether an error is retryable;
- whether a new plan is needed;
- whether an engineering action requires approval;
- whether to accept or roll back a modification;
- whether the mission is completed, failed, cancelled, or blocked.

It must not contain direct PyAEDT or PyEDB calls.

### `aedt_agent.v0`

Owns the preserved Stage A/B/C application.

Its behavior remains stable during the architecture migration. Existing demo,
benchmark, planner, and evolution tests are moved with their modules or retained
as compatibility tests. New Agent features must not be implemented inside
`v0.demo.service`.

### Shared domain packages

The existing Workflow, Node, layout, validation, knowledge, and reporting
packages remain deterministic capabilities. They execute or evaluate a
well-defined request and return structured results.

They do not own Mission state or decide the next Mission action.

### `aedt_agent.domain`

This is the future home for stable, product-independent electromagnetic domain
contracts and services. Initial additions must be driven by the BRD vertical
slice. It must not become a second copy of the current shared packages.

Candidate future contents include:

- channel objective and metric contracts;
- layout action schemas;
- artifact contracts;
- domain error taxonomy;
- evaluator interfaces.

### `aedt_agent.infrastructure`

Owns technical mechanisms with no engineering decision authority:

- SQLite persistence;
- process execution and cancellation;
- worker leases;
- filesystem artifact storage;
- AEDT process/session adapters;
- event transport.

## Dependency Rules

The intended dependency direction is:

```text
entry points
    -> agent
        -> domain contracts
        -> worker interfaces
            -> shared workflow/layout/mcp capabilities
            -> infrastructure

v0
    -> shared workflow/layout/mcp capabilities
```

The following dependencies are forbidden:

- shared domain packages importing `aedt_agent.agent`;
- infrastructure deciding whether an engineering objective passed;
- workers directly changing Mission state;
- evaluators dispatching workers;
- `v0` becoming a required dependency of the new Agent runtime;
- Pi-specific types appearing in Mission, Job, Worker, or Event contracts.

## Compatibility Plan

### Import compatibility

Moving four existing packages immediately breaks a large number of imports in
tests, scripts, and internal modules. The migration therefore uses compatibility
modules:

```python
# aedt_agent/demo/__init__.py
from aedt_agent.v0.demo import *
```

Submodules that are used directly receive explicit forwarding modules during
the compatibility period. New code must import from `aedt_agent.v0`; old import
paths are deprecated but continue to work.

Compatibility modules contain no product logic.

### CLI compatibility

Two console entry points are required:

```text
aedt-agent       -> new Agent CLI
aedt-agent-v0    -> preserved legacy CLI
```

The new CLI initially exposes runtime-oriented commands:

```text
aedt-agent mission create
aedt-agent mission run
aedt-agent mission status
aedt-agent mission resume
aedt-agent mission approve
aedt-agent mission cancel
```

The legacy CLI retains the current benchmark commands. Existing standalone
scripts remain functional during the migration and may continue importing
compatibility paths.

### Versioning

The package version remains a single project version. `v0` is an application
namespace and compatibility promise, not a separately published Python
distribution.

## Mission Runtime Boundary

The architecture migration prepares, but does not fully implement, these Agent
contracts:

### Mission

Owns:

- user goal;
- measurable acceptance criteria;
- immutable engineering constraints;
- execution and iteration budgets;
- current state;
- current plan version;
- final outcome.

### Job

Represents one leaseable execution boundary with:

- capability;
- structured input;
- timeout;
- retry policy;
- idempotency key;
- input artifact references;
- output artifact references;
- structured error result.

### Worker

Consumes a Job and returns a Job result plus evidence. A Worker does not know
whether the overall Mission is complete.

The first adapters should wrap existing capabilities rather than rewrite them:

- local-cut model build;
- AEDT solve subprocess;
- Touchstone/TDR extraction;
- channel scoring;
- approval wait/resume.

### Evaluator

Reads evidence and acceptance criteria and returns a deterministic assessment:

```text
passed
needs_adjustment
invalid_model
needs_user_input
inconclusive
```

The LLM may explain or select among allowed actions, but it is not the authority
for numeric pass/fail decisions.

### Orchestrator

Advances the Mission state machine. It is the only component that creates the
next Job, requests approval, triggers a replan, accepts a modification, rolls
back, or closes the Mission.

## First Agent Vertical Slice

The first production Agent scenario is BRD local-cut via optimization.

```text
User supplies board, nets, stackup, bbox, and target metrics
    -> validate mission input
    -> build local-cut model
    -> resolve or request port selection
    -> wait for model approval
    -> solve
    -> extract S-parameters and TDR
    -> validate model evidence
    -> score channel
    -> propose one allowed void/anti-pad adjustment
    -> wait for modification approval
    -> checkpoint and apply
    -> solve and compare
    -> accept, rollback, continue, or stop
    -> deliver engineering report
```

The existing dipole tuning demo remains a fast regression fixture. It is not the
product acceptance scenario for the Agent architecture.

## Error and Recovery Policy

Error handling is policy-driven, not delegated wholesale to the LLM.

| Error class | Default action |
| --- | --- |
| Missing or invalid user input | Wait for user input |
| Ambiguous port candidate | Wait for explicit selection |
| Workflow/schema error | Repair or replan within a bounded attempt count |
| License unavailable | Retry with backoff |
| Worker process crash | Retry from the last completed Job checkpoint |
| Solver timeout | Terminate the process and apply the configured retry policy |
| Invalid model evidence | Stop optimization and create a model-repair plan |
| Metric not achieved | Propose a constrained optimization action |
| Metric regression | Roll back to the prior accepted checkpoint |
| Budget exhausted | End with an unsuccessful engineering report |

Recovery occurs at completed Job boundaries. The runtime does not attempt to
serialize live AEDT COM handles.

## Pi Integration Strategy

### Why Pi is not introduced during the migration

Pi is not rejected. It is deferred until the ansys-agent runtime contracts are
proven.

Introducing it now would create four risks:

1. Mission, Job, Worker, Event, approval, and recovery semantics are not stable
   enough to map safely to an external runtime.
2. The main engineering difficulty is long-running, stateful, license-limited
   AEDT execution rather than ordinary LLM tool calling.
3. Pi and the current project use different primary runtimes, introducing a
   TypeScript/Python boundary before its value is measured.
4. Pi project trust and tool registration do not replace process cancellation,
   filesystem policy, AEDT operation policy, or engineering approval.

### Future integration boundary

Pi may later act as an optional Agent frontend and model-session runtime:

```text
Pi frontend/runtime
    -> Mission API
    -> Event stream
    -> Approval API
    -> Worker capability API
        -> Python ansys-agent runtime
            -> AEDT workers and domain evaluators
```

The Python runtime remains independently runnable. Pi does not own Mission
persistence, AEDT lifecycle, numeric evaluation, rollback, or engineering
approval policy.

### Pi evaluation gate

A Pi proof of concept begins only after:

- the BRD Mission can resume after a process restart;
- Worker and Event contracts have passed a real AEDT scenario;
- approval, retry, cancellation, and rollback semantics are stable;
- the native Python CLI can complete the scenario independently;
- Pi can integrate through public APIs without importing Python internals.

Pi is adopted only if the proof of concept demonstrates at least one material
benefit:

- substantially less model/session orchestration code;
- better streaming and approval interaction;
- reliable multi-provider model support;
- a clearer extension ecosystem;
- measurable reduction in maintenance burden.

The comparison must also measure added deployment complexity, cross-runtime
debugging cost, failure recovery behavior, and security boundaries.

## Migration Phases

### Phase 1: Namespace and compatibility migration

- Create `aedt_agent.v0`, `aedt_agent.agent`, `aedt_agent.domain`, and
  `aedt_agent.infrastructure`.
- Move legacy application packages into `v0`.
- Add compatibility forwarding modules.
- Split the CLI entry points.
- Keep all existing tests passing.

Acceptance:

- old scripts and imports continue to work;
- `aedt-agent-v0` preserves current CLI behavior;
- `aedt-agent` resolves to the new CLI;
- no Agent behavior is added to `v0`;
- shared execution packages have no dependency on `agent`.

### Phase 2: Agent runtime foundation

- Define Mission, Job, Event, Checkpoint, Approval, and Worker contracts.
- Implement SQLite persistence.
- Implement state transition validation.
- Implement worker registration, leases, idempotency, cancellation, and
  structured error classification.

Acceptance:

- a Mission survives service restart;
- duplicate Job execution is prevented;
- a crashed Worker lease can be recovered;
- every state change has an auditable Event.

### Phase 3: BRD model-build Mission

- Wrap the current local-cut build pipeline as a Worker.
- Persist bbox, port candidates, action plan, model project, and approval.
- Resume automatically after approval.

Acceptance:

- the Agent reaches an auditable model-review state;
- ambiguous ports produce a user decision request;
- approval resumes the same Mission without rerunning completed Jobs.

### Phase 4: Solve, evaluation, and one controlled modification

- Add solve, extraction, scoring, proposal, modification, comparison, and
  rollback Jobs.
- Allow one pre-registered void/anti-pad adjustment family.

Acceptance:

- before/after artifacts are tied to checkpoints;
- deterministic metrics drive pass/fail;
- rejected actions do not change the model;
- regressions restore the previous accepted checkpoint.

### Phase 5: Limited iterative Agent and Pi proof of concept

- Add bounded multi-iteration policy.
- Stop on success, repeated action, no improvement, unrecoverable error, or
  budget exhaustion.
- Evaluate Pi only after the native Mission passes acceptance.

Acceptance:

- the complete BRD scenario produces a final engineering delivery;
- all iterations are auditable and recoverable;
- Pi evaluation has measured adoption criteria and a documented decision.

## Testing Strategy

### Migration tests

- import compatibility for every moved package;
- old and new console entry points;
- existing Stage A/B/C test suite;
- dependency rule checks preventing shared packages from importing `agent`.

### Runtime tests

- Mission state transition table;
- SQLite transactional updates;
- event ordering;
- idempotent Job creation and execution;
- lease expiration and reclaim;
- worker cancellation;
- restart recovery;
- approval wait, approve, reject, and resume.

### Scenario tests

- fake/replay BRD Mission for fast CI;
- ambiguous port candidate;
- license retry;
- solver timeout;
- invalid model evidence;
- metric regression and rollback;
- budget exhaustion;
- one controlled successful improvement;
- real AEDT acceptance run on the supported local environment.

## Non-Goals

This migration does not include:

- rewriting WorkflowExecutor;
- moving all shared packages into `domain` immediately;
- converting every function into a Worker;
- distributed Workers;
- Redis, Celery, Kafka, or Kubernetes;
- multi-agent conversation;
- arbitrary Python execution;
- VLM-based numeric acceptance;
- automatic bbox invention;
- Pi source-code fork or deep modification;
- multi-tenant authorization.

## Success Criteria

The architecture migration is successful when:

1. The legacy product remains runnable under `aedt_agent.v0`.
2. The default `aedt-agent` entry point represents the new Agent product.
3. Shared AEDT capabilities remain tested and reusable without depending on the
   new Agent runtime.
4. A BRD Mission can progress across multiple Worker Jobs, approvals, evidence
   evaluations, and process restarts.
5. The system can distinguish completion, engineering failure, user-input
   requirements, retryable infrastructure failure, and budget exhaustion.
6. Pi can be evaluated through stable public boundaries instead of dictating
   the Python runtime architecture.
