# BRD local-cut optimization engineering playbook

This playbook captures human engineering procedure for turning a BRD and
stackup into an agent-driven BRD high-speed via optimization loop. It is meant
to be read by an orchestrator agent before planning jobs or proposing geometry
changes.

## Scope

Use this playbook when the project goal is to optimize high-speed differential
via transitions from a Cadence BRD using Ansys AEDT/PyAEDT workers.

The agent should preserve the architecture split:

- LLM/orchestrator: plan, inspect evidence, ask for approvals, propose
  structural parameters, decide whether another iteration is needed.
- Worker: perform standardized file conversion, BRD import/local cut,
  AEDT solve, Touchstone/TDR scoring, and artifact generation.

## Current Real Input Case

The first real case uses:

- BRD: `C:\Users\z3312\code\Cadence-spb-sipi-toolbox\brd\102-006060501_R01_0610-3-s19.brd`
- Simplified stackup table: `user_input/stackup_input.csv`
- Stackup converter script: `user_input/Stackup_converter.py`
- Generated AEDT-importable stackup XML: `user_input/ansys_import.xml`
- Existing Cadence board-check toolbox:
  `C:\Users\z3312\code\Cadence-spb-sipi-toolbox`
- Existing via copper raw evidence for this BRD:
  `C:\Users\z3312\code\Cadence-spb-sipi-toolbox\reports\via_copper_consistency\config_all_raw.csv`

Treat `stackup_input.csv` as simplified engineering input, not final signoff
truth. If a later original stackup becomes available, rerun stackup conversion
and mark any previous simulation evidence as based on the simplified stackup.

## Intake Workflow

1. Receive the BRD and stackup as the two required project input artifacts.
2. Convert the stackup into AEDT import XML using the project converter.
3. Open or inspect the BRD before planning optimization jobs.
4. Record which high-speed nets or channels need simulation.
5. Identify the routing layers used by the high-speed traces.
6. Identify how many components connect to those high-speed routing layers.
7. Derive the set of via-transition patterns that need optimization.

For this project, prefer using the existing Cadence toolbox output to seed the
planning artifact instead of manually inspecting every route:

```powershell
.\.venv\Scripts\python.exe scripts\summarize_via_copper_patterns.py `
  C:\Users\z3312\code\Cadence-spb-sipi-toolbox\reports\via_copper_consistency\config_all_raw.csv
```

The planner must produce an explicit planning artifact before any solve job:

- BRD path.
- Stackup source path.
- AEDT stackup XML path.
- Stackup fidelity, such as `simplified` or `original`.
- High-speed routing layers.
- Components found on each high-speed routing layer.
- Derived pattern inventory.
- Assumptions and unresolved questions.

## Cadence Toolbox Rules To Reuse

The local `Cadence-spb-sipi-toolbox` repo already encodes the board-level
inspection logic that should seed this agent workflow.

Use these sources as the engineering reference:

- `tools/via_copper_consistency/README.md`
- `tools/via_copper_consistency/via_copper_core.il`
- `tools/via_copper_consistency/via_copper/model.py`
- `tools/via_copper_consistency/via_copper/analysis.py`
- `tools/diffpair_spacing/README.md`
- `tools/diffpair_spacing/Net_Distance_Analyzer.il`

Do not ask the LLM to infer route layers or component families from names alone
when raw toolbox evidence exists.

## High-Speed Layer Identification

Use the PCB constraint system first. The board-check tools select high-speed
differential pairs with `diff_constraints`, such as `DIFF90`. The matching logic
checks the differential pair, the nets, and parent constraint groups.

For global high-speed route-layer discovery, `diffpair_spacing` uses
`layers=` empty to automatically derive the actual etch route layers from the
selected diffpairs. For the current real BRD and `DIFF90`, this produced 16
actual route layers.

For per-via optimization pattern planning, use the stricter via copper rule:

1. Collect P-side and N-side vias for each differential pair.
2. Pair P/N vias by nearest geometry.
3. For each via, use `axlDBGetConnect(via t)` and collect connected etch
   `path`, `line`, `arc`, and other non-pin/non-via etch objects.
4. Compute route layers for P and N vias.
5. Prefer the P/N intersection as the pair route layer set.
6. If there is no intersection, use the P/N union and mark the pattern as less
   clean.

This distinction matters:

- `route_layer` is the actual breakout/trace layer connected to a via pair.
- `layer` in via copper raw output is the check or difference layer being
  sampled.
- Do not group optimization patterns by check layer. Check layer is evidence;
  route layer is part of the transition pattern identity.

## Component Grouping Rule

Use the via copper component logic:

1. For each P/N via pair, compute the pair midpoint.
2. Look at pins on the selected differential nets.
3. Ignore nearby passives whose refdes starts with configured prefixes such as
   `C`, `R`, or `L`.
4. Assign the via pair to the nearest remaining component pin.
5. Record `component` from the component refdes.
6. Record `component_group` from the component package when present; otherwise
   fall back to refdes.
7. Record `component_side` from the pin start/end side, typically `TOP` or
   `BOTTOM`.

Use `component_group` as the primary pattern dimension. This intentionally
groups same-package devices even when their refdes differ. For majority-pattern
checks, do not use a component group that has fewer than two differential pairs
as a majority baseline.

## Pattern Inventory Rule

For optimization planning, define a pattern as:

```text
route_layer + component_group + padstack + via span
```

This is the most useful first-pass identity because the agent is optimizing
differential via structures, not uniform route traces.

Pattern records should include:

- `route_layer`
- `component_group`
- `padstack`
- `span`
- `diffpair_count`
- `component_count`
- `check_layers`
- representative `component` values
- representative `diffpair` values
- raw evidence artifact reference

`check_layers` should be retained for inspection and local-cut selection, but
not treated as separate optimization patterns unless human review says that a
specific layer-local copper feature must be split out.

## Work Partition Rule

After building the pattern inventory, partition the work by `component_group`.

For this board class, component-level partitioning is the preferred human
workflow because each component family has repeated via structures and a stable
set of shallow-to-deep modeling heuristics.

Use this scheduling order:

1. Split the pattern inventory by `component_group`.
2. If multiple engineers or agents are available, assign one component group to
   one owner.
3. If only one owner is available, process component groups as stages.
4. Inside each component group, sort patterns from shallow route layers to deep
   route layers using stack order.
5. For the first solve of a component group, choose one representative
   differential net from the shallowest relevant route-layer pattern.
6. Record the selected diffpair and the reason it was chosen.

The first representative net may be random within a structurally equivalent
pattern, but the selection must be recorded for audit. Do not silently switch
representative nets between build, solve, and score.

## Local-Cut Build Workflow

For each selected representative diffpair:

1. Use the BRD import/cutout tooling to cut out the required signal nets and
   reference nets.
2. Start from a net-based cutout, then manually cut a smaller local region for
   the actual simulation cell.
3. Import the generated stackup XML so AEDT uses the intended stackup.
4. Apply protocol-specific AEDT setup settings.
5. Apply component-specific geometry settings.
6. Apply port settings.
7. Apply via drill and backdrill corrections.
8. Stop at the mandatory human model-review gate before solving.

Current references:

- `src/aedt_agent/layout/import_cutout.py` contains reusable net-pattern parsing
  helpers for resolving requested nets.
- `user_input/import_brd_cutout.py` is the current PyEDB BRD import and cutout
  reference script.
- `user_input/ansys_import.xml` is the current generated stackup XML.

The orchestrator may propose a local cut region, but the first real model for a
component group must be reviewed and adjusted by a human engineer before solve.

## Existing AEDT Model Entry Point

If a human engineer has already created and reviewed a solvable AEDT local-cut
model, the workflow may start after the model-review gate.

For the current real case, an existing AEDT project is available at:

```text
C:\Users\z3312\code\Cadence-spb-sipi-toolbox\brd\102-006060501_R01_0610-3-s19\102-006060501_R01_0610-3-s19.aedt
```

This entry point skips BRD import/cutout for the first closed-loop validation
and focuses on:

1. run `brd.local_cut.solve` on the reviewed AEDT model;
2. run `brd.channel.score` on exported Touchstone/TDR artifacts;
3. let the decider inspect bounded evidence;
4. propose one small controlled geometry edit;
5. run `brd.model.edit` to create an edited AEDT project copy;
6. solve and score the edited copy;
7. compare before/after evidence and decide whether to continue.

The source AEDT project must remain read-only. Workers should copy the `.aedt`
file and its sidecar `.aedb` directory into a controlled artifact workspace
before solving or editing.

For the current simplified reviewed model, do not create a fresh AEDT bundle
for every slow solve/edit iteration. The orchestration policy is:

1. copy the human-reviewed source `.aedt/.aedb` once into a controlled remote
   working project;
2. use `project_copy_mode=working_project` for repeated solve/edit jobs against
   that copy;
3. keep only the working AEDT bundle, manifests, score evidence, curve plots,
   and optional explicit accepted checkpoints;
4. never pass the original human model as a `working_project` unless the human
   explicitly approves modifying it.

This is a process-control requirement, not just disk cleanup: too many
intermediate AEDT copies make it hard to know which geometry produced which
score.

Every optimization iteration must append to a user-readable history artifact.
The preferred artifact is `optimization_history.csv`, with one row per round.
It must include the geometry action, changed layers and parameter names,
solve status, score status, key `SDD11`/`SDD21`/TDR/objective metrics, artifact
refs, and a next-step recommendation. Do not omit partial rounds: if a solve
has produced `channel.s4p` but TDR has not yet been exported, record the row as
`needs_tdr_export_before_score`. This lets the human engineer see progress
during long solves and decide whether to continue, pause, or change direction.

## PyEDB BRD Import Troubleshooting

Known failure mode:

- The BRD can be opened in AEDT.
- The generated `.aedt` or `.aedb` visibly contains nets.
- Python sees `len(edb.nets.nets) == 0`.
- `edb.cutout()` fails with a net lookup error such as `KeyError:
  '<net_name>'`.
- Diagnostics may show `Nets:0, padstack_instances:0, primitives:0,
  components:0`.

Interpretation:

- Do not assume the net name is wrong.
- Do not assume the BRD has no nets.
- Treat this first as a PyEDB backend or Cadence environment issue.

AEDT 2026.1 can default to the PyEDB gRPC backend. That path is still risky for
this workflow: BRD translation may succeed while the Python-side EDB object does
not enumerate layout data correctly. Switching to the legacy/.NET backend has
resolved this issue in practice. AEDT 2024 and 2023 did not show this same issue
in the current experience.

Recommended command shape:

```powershell
$env:PATH='C:\Cadence\SPB_24.1\tools\bin;' + $env:PATH

.\.venv\Scripts\python.exe .\user_input\import_brd_cutout.py `
  -i your_board.brd `
  -n NET1 NET2 `
  -r GND `
  -v 2026.1 `
  --grpc false
```

Required dependencies for the legacy/.NET backend:

```powershell
uv pip install ansys-pythonnet pywin32
```

Use the project virtual environment explicitly. Do not run a bare `python`
unless it is known to be the environment with the required packages.

Cadence BRD translation depends on Cadence `extracta.exe`. If the import reports
`Extracta version could not be identified`, check whether the Cadence bin
directory is on `PATH`:

```powershell
extracta.exe -version
```

If missing, prepend:

```powershell
C:\Cadence\SPB_24.1\tools\bin
```

Script-level guardrails:

- After BRD import, close and reopen the `.aedb` to force PyEDB dictionaries to
  refresh.
- Before cutout, inspect `list(edb.nets.nets.keys())`.
- If net count is zero, fail immediately with an environment/backend diagnosis.
- Provide an explicit `--grpc false` option so the operator can force
  legacy/.NET backend.
- Do not continue to cutout when `edb.nets.nets` is empty.

## Protocol Setup Rule

Use protocol-specific setup templates. Do not let the LLM invent solver settings
from scratch.

Current reference:

- `user_input/serdes_setup.py` is a recorded AEDT 2026.1 reference setup for
  112G SerDes-style simulations.

Important settings captured in the reference:

- Design: HFSS 3D Layout setup.
- Setup name: `Setup1`.
- Sweep name: `Sweep1`.
- Sweep: `LIN 0GHz 67GHz 0.05GHz`.
- Adaptive/broadband reference includes 5 GHz and 67 GHz entries.
- Interpolating sweep with passivity enforcement.
- Gap port calibration enabled.
- HFSS extents use bbox extents, 3 mm air extension, radiation boundary, and
  5 GHz operating frequency.

Treat recorded scripts as reference settings, not as opaque policy. The worker
should eventually convert these into a structured protocol setup contract, such
as `protocol=112g_serdes`, `frequency_stop_ghz=67`, `step_ghz=0.05`,
`setup_name=Setup1`, and `sweep_name=Sweep1`.

## Via, Drill, And Backdrill Rule

Imported BRD via diameters often represent finished hole size. HFSS simulation
should usually use drill-tool diameter instead.

Use these current engineering corrections unless human review overrides them:

- Imported `0.15 mm` via hole -> simulate as `0.20 mm`.
- Imported `0.20 mm` via hole -> simulate as `0.25 mm`.
- Units: millimeters.
- If backdrill exists, set backdrill stub to `8 mil`.

Backdrill presence and effective span should be determined from Cadence
database evidence where possible. The existing `diffpair_spacing` SKILL script
uses `axlBackdrillGet(via)` and applies `topMustNotCutLayer` /
`botMustNotCutLayer` to derive the effective via span after backdrill. Reuse
that logic when turning board evidence into simulation via settings.

Do not solve a model until the drill/backdrill interpretation has passed human
review for the first representative model of the component group.

## Mandatory Initial Model Review Gate

Before the first solve for each component group, force a human checkpoint after
initial model construction.

The gate must verify or modify:

- selected component group;
- selected representative diffpair;
- signal and reference nets;
- local cut bbox and manually reduced local region;
- imported stackup XML;
- protocol setup settings;
- component dimensions and orientation;
- port locations, port type, and reference assignment;
- via drill diameter correction;
- backdrill existence and stub length;
- whether the model is small enough and physically meaningful.

Additional human-only checks:

- The cutout is large enough to include all physical structures needed for
  return current.
- If the via passes through power planes, keep the relevant power plane geometry
  in the model and usually convert that power plane reference to GND for this
  simulation setup.
- Check whether the real material stackup plus actual trace width/spacing still
  meets the intended 90 ohm target. If pitch must remain fixed, the engineer may
  slightly tune trace width to hit 90 ohm.
- If cutting the channel creates excessive P/N length mismatch, either manually
  adjust the cut to a region where P/N are better matched or select another
  layout net that is easier to cut with matched P/N length.

The agent must not claim these checks are complete based only on text evidence.
They require human inspection in AEDT/layout context. The correct automated
behavior is to stop, present the model-review packet, and wait for approval or
manual edits.

The graph must not advance from build to solve until this review is approved.
This is stricter than later optimization iterations because initial model
construction is where wrong ports, wrong stackup, wrong drill interpretation, or
over-large cutouts can invalidate all following evidence.

## Optimization Objective And Stop Rule

The optimization goal is open-ended improvement under a finite time and
iteration budget. Do not optimize only to the first passing result if useful
iteration budget remains and changes are still low risk.

Minimum acceptance targets for 112G SerDes work:

- TDR impedance window: peak and valley within target impedance +/- 9 ohm.
- Return loss: from 0 GHz to 28 GHz, RL must be below -17 dB.

For differential local-cut evidence, the score is based on a four-port
Touchstone artifact:

- export and score `channel.s4p`, not `channel.s2p`;
- return loss is `SDD11`;
- insertion is `SDD21`;
- the TDR report observes `Diff1` by default.

The LLM must determine which physical end `Diff1` corresponds to before using
TDR time ordering to map high/low impedance features to near/far geometry. It
may use AEDT port names, differential pair setup, component-side evidence, or a
human-reviewed port map, but it must record the evidence used. If orientation
is unknown, the decider may report the TDR feature but must not claim a
near/far physical cause.

Use these as floor requirements, not as the ceiling. The decider should track
best-so-far evidence and continue only while the next proposed change is small,
within constraints, and expected to improve the TDR/RL score.

For ranking iterations, use a bounded objective instead of only pass/fail or
single worst-point metrics. The current objective combines:

- RL violation in the target band: sum, max, and count of points above the RL
  target.
- TDR proximity to target impedance: mean-square and root-mean-square error to
  the target ohms value.
- TDR flatness: mean-square adjacent-step change and RMS adjacent-step change.

The worker should emit these as `rl_violation_sum_db`,
`rl_violation_max_db`, `rl_violation_point_count`,
`tdr_proximity_mse_ohm2`, `tdr_proximity_rmse_ohm`,
`tdr_flatness_msd_ohm2`, `tdr_flatness_rms_step_ohm`, and
`optimization_objective.total_cost`. Lower total cost is better. This encodes
the engineering experience that when the TDR is close to target and locally
flat, RL usually improves as well. The decider may use this total cost to rank
small reversible edits, but it must not replace the final RL and TDR acceptance
floors.

## TDR-Driven Optimization Rule

Use TDR as the primary diagnostic signal for geometry changes. RL remains a
required pass/fail and ranking metric, but the first physical interpretation
comes from where the TDR curve is high or low.

Use this first-pass physical mapping:

- TDR time zero is the impedance at the observation port for that trace. As
  TDR time increases, the reflection point is farther along the path away from
  that port. Therefore the same physical structure can appear early or late
  depending on which end of the channel is being observed.
- Before mapping a TDR high/low point to a physical via, ball, trace segment,
  or barrel region, record the TDR observation port and feature time/window.
  Do not decide "near" versus "far" from time alone without knowing which port
  the TDR was computed from.
- TDR high means the local impedance is high. First consider adding or enlarging
  non-functional pads in the mapped via-barrel region to reduce impedance.
- TDR low means the local impedance is low. First consider enlarging anti-pad
  openings in the mapped local region to increase impedance.

The decider must reason from bounded evidence only:

- TDR peak/valley value.
- TDR observation port.
- TDR port orientation evidence for `Diff1`, or an explicit `unknown` marker.
- TDR peak/valley time or time window.
- Mapped approximate structure location, interpreted from that observation
  port.
- `SDD11` worst value and frequency in the 0-28 GHz band.
- `SDD21` worst value and frequency in the 0-28 GHz band.
- RL violation summary in the target band.
- TDR proximity and flatness metrics.
- Optimization objective strategy and total cost.
- Change history and current geometry settings.
- User-provided geometry limits.

Do not send raw TDR or raw S-parameter curves to the LLM. Use artifact-only raw
data and bounded summaries or targeted window queries.

## Minimal Action Set

For the initial optimization workflow, use only two low-complexity action
families:

1. Enlarge anti-pad openings to increase local impedance.
2. Add or enlarge non-functional pads to decrease local impedance.

This intentionally avoids broad trace rerouting, stackup edits, and large
geometry redesigns. The agent should prefer the smallest change that tests the
current hypothesis.

Current placement heuristics:

- Near solder balls, the adjacent two layers often need larger anti-pad
  openings.
- Around the routed trace layer, inspect all nearby layers, including layers
  whose names look like route layers. Do not decide from `L3`, `L5`, `L7`, or
  similar names alone.
- Anti-pad enlargement is allowed on any layer where worker or human evidence
  shows a selected physical shape around the intended via/parasitic center. If
  the layer has no shape around the via, anti-pad enlargement on that layer is
  still not meaningful.
- Before editing an anti-pad on any layer, confirm that a physical shape exists
  around the via on that layer.
- In normal engineering practice, do not enlarge anti-pads by directly changing
  the padstack `antipad_by_layer`. Select the plane shape that needs clearance,
  then add parametric void geometry to that shape: circular voids centered on
  the via centers, plus the usual rectangle between the two differential via
  centers when the clearance between the pair should be opened.
- Before choosing the void center, identify the physical parasitic being reduced.
  The center must come from that structure, not from any convenient via on the
  same net. For L02/L2, the anti-pad is often for the L1 solder ball and the
  L1-L4 or L1-L2/L2-L3 laser-via pad parasitic, so use the laser-via/pad/ball
  center. When editing lower layers for buried-via parasitics, use the buried
  via center. Do not mix these centers across the stack.
- If the via is long and the impedance is high through the via barrel region,
  add non-functional pads to bring impedance down. In AEDT/HFSS 3D Layout, add
  these as explicit signal-net circle shapes on the target layers at the
  reviewed via centers. Do not rely on directly changing padstack
  non-functional pads, because AEDT can automatically remove unused
  non-functional pad entries during import or cleanup.
- Non-functional pad additions are allowed on all reviewed mechanical-hole
  layers when the TDR feature maps to excess via-barrel inductance. When
  middle-layer non-functional pads have been removed, the barrel inductance
  rises as the hole gets deeper, which can push the local impedance high.
  Adding explicit signal-net pads on selected internal layers is the first
  controlled way to lower that impedance.

The wording "near" must be translated into explicit layer names by the worker or
by human review using the selected pattern's `route_layer`, `span`, component
side, stack order, and backdrill/effective-span evidence.

## Optimization Constraints Required At Intake

The agent should ask for geometry limits at the beginning of the project or
before the first optimization proposal for a component group.

Required limits:

- Anti-pad min/max diameter or clearance per relevant layer class.
- Allowed anti-pad step size.
- Non-functional pad min/max diameter per relevant layer class.
- Allowed non-functional pad step size.
- Whether non-functional pads are allowed on power/ground layers.
- Whether P and N must be changed symmetrically.
- Minimum manufacturable clearance to nearby copper, vias, and pads.
- Maximum number of changed layers per iteration.

The LLM must not invent these limits. If limits are missing, it may propose a
hypothesis but must request human-provided bounds before generating executable
geometry actions.

Current user-approved geometry limits for the first optimization pass:

- Anti-pad circular void radius: maximum `22mil`
  (`constraints.max_diameter = 44mil` in worker handoff).
- Non-functional pad explicit signal-net circle radius: minimum `7.875mil`,
  maximum `10mil` once the pad is added
  (`constraints.min_diameter = 15.75mil`,
  `constraints.max_diameter = 20mil` in worker handoff).
- Any proposal outside these radius limits is not executable and must return to
  a human approval gate before worker execution.

## Candidate Action Inventory Contract

The reviewed optimization loop must not infer editable layers from any example
action. Before the first solve, `candidate_inventory_builder` preserves
`candidate_action_inventory` or `candidate_action_inventory_path` as reviewed
facts and also builds deterministic fallback actions. With LLM configured, the
decider should use this inventory, the playbook, and bounded score evidence to
propose the next `selected_action` itself instead of blindly choosing a
prewritten action.

Use `anti_pad_shape_layers` for every reviewed layer where a selected physical
shape exists around the intended via or parasitic center. Use
`non_functional_pad_layers` for every reviewed mechanical-hole layer where an
explicit signal-net circle pad is allowed. A layer can be L2, L5, L7, a route
named layer, or any other layer; eligibility comes from selected shape or
mechanical-hole evidence, not the layer name.

In the run config, prefer a path rather than layer-specific inline data:

```json
{
  "candidate_action_inventory_path": "D:/aedt-agent-runs/reviewed-loop/candidate_action_inventory.json"
}
```

The inventory file contains reviewed facts:

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

Explicit `candidate_actions` are still accepted for hand-authored cases, but
they are seed/fallback actions, not a layer allow list. LLM proposals must stay
inside reviewed inventory facts; the geometry validator remains the executable
handoff gate.

## Optimization Proposal Contract

Each proposal must be small, structured, and reversible.

Required fields:

- `hypothesis`: what TDR/RL feature the change addresses.
- `evidence_refs`: score/evidence artifacts used.
- `tdr_observation_port`: which port the TDR trace is looking from.
- `tdr_feature_time`: feature time or time window used to map the physical
  location.
- `target_region`: solder-ball region, route-adjacent region, via-barrel
  region, or another reviewed region.
- `action_type`: `anti_pad.enlarge` or `non_functional_pad.add_or_enlarge`.
- `layers`: explicit layer names.
- `delta`: proposed size change with units.
- For `anti_pad.enlarge`: selected `plane_shape_ids`, center references or
  reviewed center coordinates, and target circular void diameter or radius.
  When needed, also include
  `bridge_between_vias: true` so the worker adds the rectangle between the two
  via centers. If the circular void handoff lists more than two centers, also
  include `bridge_center_padstack_instance_ids` or `bridge_via_centers` with
  exactly two reviewed centers for the rectangle bridge.
- Each anti-pad proposal must state `parasitic_target` and `center_source`.
  Prefer `center_padstack_instance_ids` so the worker resolves the center from
  EDB. Use literal `via_centers` only when a human has reviewed that those
  coordinates are the center of the intended parasitic object.
- If the radius should remain editable in AEDT, include `parameter_name` with a
  valid design variable name and provide `target_radius`. The worker will create
  or update the design variable, use it as the circle radius, and use it as the
  bridge rectangle width.
- Parameterized bridge rectangles must be AEDT Rectangle primitives, not polygon
  primitives with expression points. For axis-aligned differential via pairs,
  the same rule applies on L02 and all other selected plane-shape layers: if the
  two bridge centers share y, the engineering start point is
  `(left_via.x, left_via.y + r_void)` and the engineering end point is
  `(right_via.x, right_via.y - r_void)`. If the two bridge centers share x,
  swap x/y: the rectangle spans the two y coordinates and uses `x +/- r_void`.
  The worker passes lower-left/upper-right coordinates to PyEDB but records the
  engineering start/end points in the manifest. Do not add
  `bridge_length_factor` or a separate bridge-length variable for this standard
  center-to-center tangent bridge.
- For `non_functional_pad.add_or_enlarge`: target layers, via center source,
  signal nets or `center_padstack_instance_ids`, and target explicit circle
  diameter or bounded radius/delta. The worker should draw signal-net circle
  shapes on the layers. Direct padstack `pad_by_layer` edits are legacy
  diagnostics only and should not be the default optimization action. The
  proposal must identify the reviewed mechanical-hole barrel region or other
  via-barrel mechanism being addressed.
- For all executable geometry proposals, include the current user-approved
  radius limits in the worker `constraints`: anti-pad `max_diameter=44mil`;
  non-functional pad `min_diameter=15.75mil` and `max_diameter=20mil`.
- Every TDR-driven proposal must identify the observation port and feature
  time/window. If the observation port is missing, the decider must not map the
  TDR feature to a physical structure or propose geometry edits from that
  feature.
- `constraints_checked`: user limits referenced by id or value.
- `expected_effect`: increase or decrease impedance, plus expected RL impact.
- `risk`: likely side effects.
- `rollback`: previous geometry values or artifact reference.

If an anti-pad proposal cannot name explicit layers, selected plane shapes, via
centers, and a bounded void size, it is not ready for worker execution.

## Controlled Model Edit Rule

The first real model-edit worker is `brd.model.edit`. It is intentionally narrow
and edits only copied AEDT projects.

Allowed edit targets:

- selected physical-shape void geometry for `anti_pad.enlarge` on any layer
  where the selected shape exists around the via center;
- explicit signal-net circle shapes for
  `non_functional_pad.add_or_enlarge`, centered on reviewed via centers.

Required worker input:

- source `project_path` pointing to an existing `.aedt`;
- sidecar `.aedb` beside the source `.aedt`;
- `action_type`;
- explicit `layers`;
- for `anti_pad.enlarge`: selected plane shape ids, `parasitic_target`,
  center source, center padstack instance ids or reviewed coordinates, and
  `target_diameter`, `void_diameter`, or radius;
- for `non_functional_pad.add_or_enlarge`: explicit layers,
  `center_padstack_instance_ids` or reviewed coordinates plus signal nets, and
  `target_diameter` or `target_radius` for the circle shape;
- user constraints such as min/max diameter and max delta.

Current implementation details:

- The worker copies the `.aedt` and `.aedb` into the artifact directory before
  editing.
- The source project is checked after the edit and must remain unchanged.
- Raw edited project data is artifact-only.
- The worker emits `model_edit_manifest.json`, `edited_project_path`,
  `edited_edb_path`, and bounded change records.
- If a layer name differs only by zero padding, such as `L5` versus `L05`, the
  worker may resolve it when the match is unique.
- `anti_pad.enlarge` requires shape presence verification: the via center must
  be inside a selected shape before the void is created. Layer names are not
  used as a hard allow/deny list.
- `anti_pad.enlarge` also requires `parasitic_target`. When
  `center_padstack_instance_ids` are supplied, the worker resolves unique
  centers directly from EDB padstack instance positions and records the source
  instances in the manifest. This is preferred over hand-entered coordinates.
- `non_functional_pad.add_or_enlarge` defaults to explicit signal-net circle
  shapes. When `center_padstack_instance_ids` are supplied, the worker resolves
  both the center and signal net from each EDB padstack instance, then creates a
  circle copper shape on the requested layer. Legacy direct padstack edits must
  be explicitly requested and are not considered a valid default optimization
  path for AEDT-reviewed models.
- Before proposing `non_functional_pad.add_or_enlarge`, confirm that the target
  padstack instance/layer belongs to a reviewed mechanical-hole via barrel. The
  expected physical effect is to reduce high impedance caused by excess barrel
  inductance after internal non-functional pads were removed.

For the current reviewed model, a correct parameterized L02/L2 anti-pad edit
shape is:

```json
{
  "action_type": "anti_pad.enlarge",
  "parasitic_target": "l1_ball_and_l1_l4_laser_via_pad",
  "center_source": "padstack_instances",
  "center_padstack_instance_ids": [
    4294981993,
    4294981994,
    4294982001,
    4294982002
  ],
  "bridge_center_padstack_instance_ids": [
    4294981993,
    4294982001
  ],
  "layers": ["L2_GND"],
  "plane_shape_ids": [173575],
  "target_radius": {"value": 20, "unit": "mil"},
  "parameter_name": "l02_void_r",
  "bridge_between_vias": true
}
```

The `plane_shape_ids` must come from model inventory or human selection, and the
center references must correspond to the physical parasitic being reduced. The
worker creates two circular voids and, when requested, one rectangle between the
resolved centers, without modifying the source project.

For L02/L2 edits, do not reuse the `L4_GND-L31_GND` long-via centers. In this
reviewed model the L2 signal parasitic centers come from the L2 laser-via
padstack instances: `via_234872`/`via_234873` for DP0 and
`via_234880`/`via_234881` for DN0:

```text
DP0 L2 center: x=63.178182mm, y=299.946568mm
DN0 L2 center: x=64.078104mm, y=299.946568mm
```

For this reviewed L02 case, the bridge centers have the same y coordinate and
the pitch is approximately `0.899922 mm` (`35.43 mil`). The bridge rectangle
therefore spans x=`63.178182mm` to x=`64.078104mm` and y=`299.946568mm +/-`
`l02_void_r`. The same center-to-center tangent rule applies to deeper selected
shape layers; only the reviewed parasitic centers and target layer change.

## Current Real Case Baseline

Existing `via_copper_consistency/config_all_raw.csv` for
`102-006060501_R01_0610-3-s19.brd` used `DIFF90` and `auto_route_stack`.

Observed compact baseline:

- Raw sample rows: 249,764.
- Matched via-copper differential pairs: 1,025.
- Route layers: `L03`, `L04_GND`, `L05`, `L07`, `L09`, `L11`, `L13`,
  `L15`, `L20`, `L22`, `L24`, `L26`, `L28`, `L30`, `L31_GND`, `L32`.
- Component groups: `BGA_800P_0_9_23_1X30_1X3_44`, `MEZZ_2189161115`,
  `QSFP_DD_2162520003`.
- Pattern count using `route_layer + component_group + padstack + span`: 45.

Do not conflate this with the `diffpair_spacing` count. That tool reports
1,153 matched DIFF90 diffpairs and the same 16 route layers for spacing
analysis. The via copper count is lower because it filters through via-pair and
component-majority suitability.

## Stackup Conversion Rule

Use `Stackup_converter.py` to generate `ansys_import.xml` from the stackup
table before AEDT import.

The converter output is an AEDT stackup control XML containing materials,
dielectrics, copper layers, soldermask, thicknesses, DK, and DF values. Workers
may import this XML into AEDT, but the orchestrator must still track the source
stackup file and whether it was simplified.

Do not let the LLM invent missing material values. If DK/DF/thickness is absent
or ambiguous, preserve the uncertainty in the planning artifact and ask for
human confirmation before using the result as final-quality evidence.

## Pattern Planning Rule

The layout traces are assumed to be uniform for this project class. Therefore:

- Do not optimize trace routing geometry in the first optimization loop.
- Do not propose arbitrary trace width, spacing, or routing-layer changes unless
  the human engineer explicitly expands the scope.
- Optimize differential via structures and their local return/reference
  environment.

Define optimization patterns from high-speed routing layers and connected
components:

1. Find the high-speed routing layers.
2. For each high-speed routing layer, find the components connected by the
   relevant high-speed signals.
3. Group structurally equivalent differential via transitions into one pattern.
4. Optimize each pattern independently unless evidence shows patterns are
   coupled.

A pattern should describe the transition being optimized, not an individual
trace segment. At minimum, record:

- Pattern id.
- Signal or channel family.
- Component or component pair.
- Source layer and destination layer when known.
- Differential via transition type.
- Reference/return layers involved.
- Candidate local-cut region, or a note that bbox approval is still required.
- Count of matching instances in the BRD.

## Human Approval Boundaries

The agent must not guess these items:

- Final local-cut bbox.
- Which high-speed channels are in scope if the BRD contains multiple families.
- Stackup values missing from the source stackup.
- Whether simplified stackup evidence is acceptable for a design decision.
- Whether trace geometry is allowed to change.
- Geometry limits for anti-pad and non-functional-pad edits.
- Whether non-functional pads are allowed on specific power/ground layers.
- Whether P/N geometry must remain strictly symmetric.

When unsure, pause at the planning/approval gate with a concise request for the
specific missing engineering decision.

## Worker Handoff Expectations

For this planning phase, the orchestrator should hand workers structured inputs,
not prose-only instructions.

Recommended planning output shape:

```json
{
  "brd_path": "C:\\Users\\z3312\\code\\Cadence-spb-sipi-toolbox\\brd\\102-006060501_R01_0610-3-s19.brd",
  "stackup_source": "user_input/stackup_input.csv",
  "stackup_xml": "user_input/ansys_import.xml",
  "stackup_fidelity": "simplified",
  "route_layer_source": "via_copper_raw:auto_route_stack",
  "via_copper_raw": "C:\\Users\\z3312\\code\\Cadence-spb-sipi-toolbox\\reports\\via_copper_consistency\\config_all_raw.csv",
  "high_speed_layers": [
    "L03",
    "L04_GND",
    "L05",
    "L07",
    "L09",
    "L11",
    "L13",
    "L15",
    "L20",
    "L22",
    "L24",
    "L26",
    "L28",
    "L30",
    "L31_GND",
    "L32"
  ],
  "component_groups": [
    "BGA_800P_0_9_23_1X30_1X3_44",
    "MEZZ_2189161115",
    "QSFP_DD_2162520003"
  ],
  "pattern_key_fields": [
    "route_layer",
    "component_group",
    "padstack",
    "span"
  ],
  "patterns": [
    {
      "route_layer": "L04_GND",
      "component_group": "BGA_800P_0_9_23_1X30_1X3_44",
      "padstack": "BBVIA_L4_L27_HS",
      "span": "ETCH/L04_GND-ETCH/L31_GND",
      "diffpair_count": 900,
      "component_count": 16
    }
  ],
  "work_partition": {
    "primary_axis": "component_group",
    "route_layer_order": "shallow_to_deep",
    "first_model_strategy": "one_representative_diffpair_per_component_group"
  },
  "model_review_gate": {
    "required_before_first_solve_per_component_group": true,
    "checks": [
      "local_cut_bbox",
      "stackup_xml_imported",
      "protocol_setup_applied",
      "component_dimensions",
      "ports",
      "drill_diameter_correction",
      "backdrill_stub",
      "return_path_physical_entities",
      "power_plane_reference_handling",
      "trace_impedance_with_real_stackup",
      "pn_length_match_after_cutout"
    ]
  },
  "optimization_objective": {
    "strategy": "best_within_budget",
    "primary_diagnostic": "tdr",
    "ranking_terms": [
      "rl_violation_sum_db",
      "tdr_proximity_mse_ohm2",
      "tdr_flatness_msd_ohm2",
      "optimization_objective.total_cost"
    ],
    "touchstone_kind": "s4p",
    "return_loss_trace": "SDD11",
    "insertion_loss_trace": "SDD21",
    "tdr_observation_port": "Diff1",
    "acceptance_floor": {
      "tdr_peak_valley_ohm": "+/-9",
      "rl_band_ghz": [0, 28],
      "rl_max_db": -17
    }
  },
  "allowed_optimization_actions": [
    "anti_pad.enlarge",
    "non_functional_pad.add_or_enlarge"
  ],
  "artifact_policy": {
    "raw_sparameters": "artifact_only",
    "raw_tdr": "artifact_only",
    "project_copy_mode": "working_project_after_initial_review_copy",
    "required_plots": ["tdr", "sdd11", "sdd21"],
    "required_final_report": true,
    "required_optimization_history_csv": true
  },
  "required_geometry_limits": [
    "anti_pad_min_max_by_layer",
    "anti_pad_step",
    "non_functional_pad_min_max_by_layer",
    "non_functional_pad_step",
    "power_ground_layer_policy",
    "pn_symmetry_policy",
    "minimum_clearance",
    "max_changed_layers_per_iteration"
  ],
  "optimization_proposal_schema": {
    "required_fields": [
      "hypothesis",
      "evidence_refs",
      "target_region",
      "action_type",
      "layers",
      "delta",
      "constraints_checked",
      "expected_effect",
      "risk",
      "rollback"
    ]
  },
  "assumptions": [
    "Layout traces are uniform; first optimization scope is differential vias only."
  ],
  "open_questions": []
}
```

## Current Open Questions

These should be resolved as the human engineer continues describing the workflow:

- Which component identifiers and high-speed net families are in scope for the
  first real case?
- Should stackup conversion be a worker capability with a formal artifact
  contract, or remain a preprocessing utility for now?
- Which pattern should be used for the first true AEDT local-cut solve smoke?
- What local-cut bbox should be approved for that first pattern?
- How should component dimensions be sourced for each component group:
  manually entered, extracted from BRD/EDB, or taken from package rules?
- Should the first import/cutout worker default to `--grpc false` for AEDT
  2026.1, or choose backend from execution profile?
- What remaining geometry limits should be used for each component group and
  layer class, such as step size, minimum clearance, power/ground-layer policy,
  P/N symmetry policy, and maximum changed layers per iteration? Current radius
  bounds are known for the first optimization pass: anti-pad radius <= `22mil`;
  non-functional pad radius in [`7.875mil`, `10mil`] once added.
