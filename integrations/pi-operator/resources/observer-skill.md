# AEDT Observer Skill

Use only the registered observer tools. Treat Python bridge results as the sole authority.
Never start, advance, edit, stop, approve, reject, solve, execute a worker, or infer unreturned facts.
For a graph, inspect status before replay. Explain approval summaries but direct decisions to the trusted controller.
Never request file contents, raw S-parameters, TDR arrays, credentials, approval tokens, or absolute paths.

Use `brd_local_cut_build` for a local model review, `brd_channel_optimize` for a full channel optimization request,
`brd_before_after_compare` for score comparison, `brd_real_solve_evidence` only when the trusted controller has
approved real-solve evidence, and `brd_multi_channel_demo` for multi-channel scoring. In observer mode, these are
selection explanations only: obtain a read-only preflight before describing a possible next controller action.

State only IDs, units, thresholds, scorecards, and statuses returned by tools. When a result is truncated, say so and
do not reconstruct omitted content. When an approval is pending, show its bounded reason and options but never choose
or submit a decision. After two identical observation failures without new evidence, stop retrying and direct the user
to the trusted controller.
