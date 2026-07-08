---
name: ansys-brd-via-optimization
description: Plan and execute agent-driven high-speed differential via optimization from Cadence BRD and stackup inputs using Ansys AEDT/PyAEDT workers. Use when the user asks for BRD local cuts, 112G SerDes or PCIe channel via optimization, stackup XML import, TDR/RL scoring, pattern planning from route layers/components, or bounded anti-pad/non-functional-pad geometry proposals.
---

# Ansys BRD Via Optimization

## Overview

Use this skill to preserve the human engineer's BRD local-cut process while
keeping the agent architecture intact: the LLM plans, judges, proposes, and
asks for approvals; workers perform standardized conversion, cutout, solve,
score, and artifact generation.

Before planning or proposing geometry changes, read the project playbook:

```text
../../../docs/agent_playbooks/brd-local-cut-optimization.md
```

## Workflow

1. Intake the two required inputs: Cadence BRD and stackup. For the current real
   case, use the paths in the playbook and generate/import `ansys_import.xml`
   from `stackup_input.csv`.
2. Build a planning artifact before any AEDT solve. Include high-speed route
   layers, component groups, pattern inventory, stackup fidelity, assumptions,
   and open questions.
3. Seed pattern planning from the Cadence toolbox evidence when available. The
   first pattern key is `route_layer + component_group + padstack + span`.
4. Partition work by `component_group`, then process route layers from shallow
   to deep. Record the representative diffpair selected for each first model.
5. Build the local cut, import stackup XML, apply protocol setup, set component
   and port details, correct drill/backdrill, then stop at the human model
   review gate before the first solve for each component group.
6. Run AEDT solve workers and channel score workers only after required gates
   are approved. Keep raw Touchstone/TDR data as artifacts; send only bounded
   evidence to the LLM.
7. Build a bounded candidate action inventory before the first solve. This
   inventory provides reviewed layer/shape/center facts and fallback actions;
   the LLM decider should still use the playbook and bounded evidence to judge
   which executable anti-pad/NFP proposal to make.
8. Optimize from TDR-driven hypotheses under user-provided geometry limits.
9. For the current reviewed differential local-cut loop, treat the primary
   Touchstone artifact as four-port `s4p`, not `s2p`. Score return loss on
   `SDD11`, insertion on `SDD21`, and use `Diff1` as the default TDR
   observation port unless model evidence shows a different differential port.

## Hard Rules

- Do not put raw S-parameter or full TDR curves into the LLM context. Use
  artifact-only raw data plus bounded summaries or targeted artifact queries.
- Do not score differential channels from single-ended `S11/S21` when a valid
  four-port artifact is available. The bounded evidence must name the
  return-loss trace (`SDD11`) and insertion-loss trace (`SDD21`).
- Do not create a new AEDT project bundle for every solve/edit iteration. Copy
  the human-reviewed source model once into a controlled working project, then
  repeatedly solve/edit that working copy while recording manifests and
  checkpoints.
- Do not finish a closed-loop optimization without a report artifact. The
  report must list the accepted/rejected geometry changes, final bounded
  metrics, plots for TDR, `SDD11`, and `SDD21`, and an optimization history
  CSV so the user can judge current progress and whether to continue.
- Do not let the best result exist only as metrics. After each scored round,
  preserve the best-so-far AEDT project bundle (`.aedt`, `.aedb`, and
  `.aedtresults` when present) under the run/report directory, overwriting only
  that best bundle when a lower `optimization_objective.total_cost` is found.
- Do not let the LLM invent route layers, component families, stackup values,
  anti-pad limits, non-functional-pad limits, or manufacturability clearances.
- Do not treat the example L2 candidate as a hardcoded allow list. The reviewed
  loop must use `candidate_action_inventory` or explicit `candidate_actions` to
  enumerate every reviewed shape-backed layer and mechanical-hole NFP layer.
- Do not advance from the first build to solve for a component group without
  human model review in AEDT/layout context.
- Do not optimize route traces in the first loop. The layout traces are treated
  as uniform; optimize differential via structures and local return/reference
  geometry.
- If PyEDB sees zero nets after BRD import even though AEDT can open the board,
  diagnose backend/Cadence environment first. For AEDT 2026.1, prefer the
  legacy/.NET backend path with `--grpc false` as described in the playbook.

## Optimization Rule

The objective is best-so-far improvement within a finite time or iteration
budget. The 112G SerDes floor is:

- TDR peak and valley within target impedance +/- 9 ohm.
- Return loss from 0 GHz to 28 GHz below -17 dB.

Treat these as minimum acceptance targets, not a stopping ceiling. Continue only
while the next change is small, reversible, within user constraints, and expected
to improve the bounded evidence.

Rank candidates with a bounded objective that combines RL violation and TDR
shape quality. The scoring worker should expose:

- `rl_violation_sum_db`, `rl_violation_max_db`, and violation point count in the
  target RL band.
- `tdr_proximity_mse_ohm2` / `tdr_proximity_rmse_ohm`, measuring closeness to
  the target impedance.
- `tdr_flatness_msd_ohm2` / `tdr_flatness_rms_step_ohm`, measuring local
  smoothness of the TDR curve.
- `optimization_objective.total_cost`, where lower is better.

This encodes the engineering rule that a flatter TDR curve usually improves RL.
The decider should prefer lower total cost when pass/fail status is unchanged,
while still treating RL and TDR acceptance floors as required signoff gates.

Use TDR as the primary physical diagnostic:

- TDR time zero is the impedance at the observation port. As TDR time
  increases, the reflection point is farther along the path away from that
  port. Before mapping a TDR high or low point to a physical via/ball/trace
  region, identify which port the TDR trace is looking from; the same physical
  structure appears at different times when viewed from the opposite port.
- In the current reviewed differential setup, the TDR observation port should
  default to `Diff1`. The decider must still determine which physical end
  `Diff1` represents from model/port evidence before mapping early or late TDR
  features to near/far geometry.
- TDR high means local impedance is high. First consider adding or enlarging
  non-functional pads in the mapped via-barrel region.
- TDR low means local impedance is low. First consider enlarging anti-pad
  openings in the mapped local region.
- Near solder balls, check the adjacent two layers for anti-pad enlargement.
- Around a route layer, inspect every layer near the relevant via, including
  layers whose names look like route layers. Do not decide from the layer name
  alone. Anti-pad enlargement is allowed on any layer where the worker or human
  evidence shows a selected physical shape around the intended via/parasitic
  center.
- Anti-pad enlargement is a selected shape void operation, not a default
  padstack `antipad_by_layer` operation. First confirm that the chosen layer has
  a physical shape around the via. Then create circular voids centered on the
  via centers; when enlarging between a differential via pair, add the usual
  bridge rectangle between the two centers.
- The void center must be the center of the parasitic structure being fixed,
  not merely any via center on the same signal. For L1/L2 regions, this may be
  the solder ball or L1-L4/L1-L2 laser-via pad center. For deeper layers, it may
  be the buried-via center. State the `parasitic_target` and prefer
  `center_padstack_instance_ids` so the worker resolves centers from EDB.
- For long vias with high impedance through the barrel, check whether
  non-functional pads should be added or enlarged. In AEDT/HFSS 3D Layout,
  do this by drawing explicit signal-net circle shapes on the target layers at
  the reviewed via centers; do not rely on changing padstack non-functional
  pads because AEDT can remove them during import/cleanup.
- Non-functional pads are allowed on all reviewed mechanical-hole layers when
  the TDR feature maps to excess via-barrel inductance. When middle-layer
  non-functional pads have been removed, deeper mechanical holes have higher
  barrel inductance, which raises local impedance; adding explicit signal-net
  pads on selected internal layers is the small first-pass way to lower that
  impedance.

The initial action set is intentionally narrow:

```text
anti_pad.enlarge
non_functional_pad.add_or_enlarge
```

Current user-approved geometry constraints for this first optimization pass:

- Anti-pad circular void radius must not exceed `22mil`.
- Non-functional pads, once added as explicit signal-net circle shapes, must
  use radius in [`7.875mil`, `10mil`].
- Proposals outside these radius limits are not executable; they must request
  human approval before any worker handoff.

## Candidate Inventory Contract

Before `optimization_decider`, the reviewed loop first runs
`candidate_inventory_builder` as an AEDB discovery worker. It opens the working
model and writes reviewed geometry facts such as shape ids, padstack instance
ids, via centers, bridge-center pairs, and signal nets into
`candidate_action_inventory_path`. Then `candidate_action_builder` expands those
facts into deterministic fallback `candidate_actions`. When LLM is configured,
the decider may propose a fresh `selected_action` from those discovered facts
instead of merely choosing a fallback action index.

In the run config, prefer a path rather than layer-specific inline data:

```json
{
  "candidate_action_inventory_path": "D:/aedt-agent-runs/reviewed-loop/candidate_action_inventory.json"
}
```

The inventory path can start as a small human scope seed:

```json
{
  "source": "human_scope_seed_for_aedt_model_discovery",
  "tdr_observation_port": "Diff1",
  "tdr_port_orientation_evidence": "reviewed port map",
  "anti_pad_shape_layers": ["L2_GND", "L4_GND"],
  "non_functional_pad_layers": ["L5", "L7"]
}
```

The discovery worker turns that seed into executable reviewed facts:

```json
{
  "source": "human_reviewed_shape_inventory",
  "tdr_observation_port": "Diff1",
  "tdr_port_orientation_evidence": "reviewed port map",
  "anti_pad_shape_layers": [
    {
      "layer": "L5",
      "plane_shape_ids": [123],
      "center_padstack_instance_ids": [501, 502],
      "bridge_center_padstack_instance_ids": [501, 502],
      "parasitic_target": "reviewed buried-via pad parasitic",
      "target_radius": {"value": 22, "unit": "mil"}
    }
  ],
  "non_functional_pad_layers": [
    {
      "layer": "L7",
      "center_padstack_instance_ids": [701, 702],
      "signal_nets": ["TX_P", "TX_N"],
      "parasitic_target": "reviewed mechanical-hole barrel inductance",
      "target_radius": {"value": 7.875, "unit": "mil"}
    }
  ]
}
```

Do not treat plain layer-name lists as final executable inventory. A layer list
is only a scope hint for the discovery worker. The final inventory handed to
`candidate_action_builder` must contain object entries with reviewed geometry
facts: layer name, selected shape ids for anti-pad actions, reviewed padstack
centers or reviewed coordinates, parasitic target, and bounded radius
information. If the final inventory is missing, invalid JSON, or produces zero
executable actions, the loop must fail before running another slow AEDT solve.

List all reviewed layers that have selected shape evidence near the intended
via/parasitic center. The LLM may choose L2, L5, L7, or any other reviewed
layer and set the action size/type from the playbook, but it must not invent
layer names, shape ids, padstack ids, ports, or geometry limits outside this
inventory. The validator and worker remain the execution gate.

## Proposal Contract

Every executable geometry proposal must be structured and bounded:

```json
{
  "hypothesis": "TDR feature and physical interpretation",
  "evidence_refs": ["score artifact or bounded query ids"],
  "tdr_observation_port": "port used for the TDR trace",
  "tdr_port_orientation_evidence": "how the agent determined which physical end Diff1 observes",
  "tdr_feature_time": {"value": 0.0, "unit": "ns"},
  "target_region": "solder_ball | route_adjacent | via_barrel | reviewed_other",
  "action_type": "anti_pad.enlarge | non_functional_pad.add_or_enlarge",
  "layers": ["explicit layer names"],
  "plane_shape_ids": ["required for anti_pad.enlarge unless explicitly auto-selected"],
  "parasitic_target": "physical parasitic being reduced",
  "center_source": "padstack_instances | manual_reviewed_coordinates",
  "center_padstack_instance_ids": ["preferred ids for center lookup"],
  "bridge_center_padstack_instance_ids": ["exactly two ids when bridge_between_vias is true and more than two centers are listed"],
  "via_centers": [{"x": 0.0, "y": 0.0, "unit": "mm"}],
  "target_diameter": {"value": 0.0, "unit": "mm"},
  "target_radius": {"value": 0.0, "unit": "mil"},
  "parameter_name": "optional_aedt_design_variable_for_void_radius",
  "bridge_between_vias": false,
  "delta": {"value": 0.0, "unit": "mm"},
  "constraints_checked": ["user limit ids or literal bounds"],
  "expected_effect": "increase_impedance | decrease_impedance",
  "risk": "side effects to watch",
  "rollback": "previous values or artifact reference"
}
```

For `anti_pad.enlarge`, the executable proposal must name explicit layers,
selected plane shape ids, the physical parasitic target, a center source,
target void diameter or radius, and the user limits it checked. Prefer
`center_padstack_instance_ids`; use manual `via_centers` only after human review
confirms they are the centers of the intended parasitic. If more than two
centers are listed for circular voids and `bridge_between_vias=true`, provide
`bridge_center_padstack_instance_ids` or `bridge_via_centers` with exactly two
reviewed centers for the rectangle bridge. For
`non_functional_pad.add_or_enlarge`, it must name the target layers, via center
source, signal nets or `center_padstack_instance_ids`, bounded circle diameter
or radius, and limits checked. Otherwise it is not ready for worker execution.
The worker creates explicit signal-net circle shapes on those layers. Direct
padstack `pad_by_layer` edits are legacy diagnostics only, not the default
engineering path. The proposal must identify the reviewed mechanical-hole
barrel region or other via-barrel mechanism being addressed.

Every TDR-driven proposal must also name `tdr_observation_port` and the feature
time/window used for mapping. Do not infer "near" or "far" from time alone
without the observation port, because the physical order reverses when the
channel is viewed from the opposite end.

For this differential workflow, bounded score evidence should include:
`touchstone_kind=s4p`, `return_loss_trace=SDD11`,
`insertion_loss_trace=SDD21`, `tdr_observation_port=Diff1`,
`tdr_port_orientation_evidence`, worst `SDD11` in 0-28 GHz, worst `SDD21` in
band, TDR peak/valley deviation, TDR proximity/flatness metrics, RL violation
metrics, `optimization_objective.total_cost`, and artifact refs for the raw
Touchstone, raw TDR, and plots.

Every closed-loop report handoff must also include `optimization_history_csv`.
Each row should represent one round and include the action taken, changed
layers/parameters, solve status, score status, key RL/TDR/objective metrics,
artifact refs, and the next-step recommendation. Incomplete rounds are valid:
for example, a solved `s4p` without TDR should be logged as
`needs_tdr_export_before_score` rather than omitted.

Executable proposals must include radius constraints in worker-ready form:
`anti_pad.enlarge.constraints.max_diameter = 44mil`; for
`non_functional_pad.add_or_enlarge`, `constraints.min_diameter = 15.75mil` and
`constraints.max_diameter = 20mil`.

When creating a bridge rectangle, use a true AEDT Rectangle primitive for
parameterization, not a polygon with expression points. The generic axis-aligned
rule applies on L02 and all other selected plane-shape layers: if the two bridge
via centers have the same y coordinate, the engineering start point is
`(left_via.x, left_via.y + r_void)` and the engineering end point is
`(right_via.x, right_via.y - r_void)`. If the two centers have the same x
coordinate, swap x and y: the rectangle spans the two y coordinates and uses
`x +/- r_void`. The worker stores lower-left/upper-right points for PyEDB but
records these engineering start/end points in the manifest. Do not request
`bridge_length_factor` or a separate bridge-length variable for this standard
center-to-center tangent bridge.

## Useful Project Artifacts

- Detailed playbook:
  `docs/agent_playbooks/brd-local-cut-optimization.md`
- Pattern summarizer:
  `scripts/summarize_via_copper_patterns.py`
- Remote solve-to-score smoke:
  `scripts/smoke_ssh_real_solve_score.py`
- Reviewed-model closed-loop runner:
  `docs/agent_templates/brd_reviewed_model_optimize_loop.yaml`
- Remote run instructions:
  `docs/remote-reviewed-model-loop.md`
- Closed-loop report generator:
  `scripts/generate_brd_optimization_report.py`
- BRD cutout reference:
  `user_input/import_brd_cutout.py`
- 112G SerDes setup reference:
  `user_input/serdes_setup.py`
