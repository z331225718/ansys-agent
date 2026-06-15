# W3 Evidence Loop Implementation Plan

> Minimal plan — core infrastructure already exists in channel_scoring.py, artifact_query.py, spectral.py.

**Goal:** Wire existing channel scoring + before/after comparison into agent graph workers. Support recorded (offline) artifacts for demo without live AEDT.

## Task 1: Recorded Evidence Worker

New worker `brd.evidence.load` that loads pre-recorded Touchstone/TDR from a directory.

- `src/aedt_agent/agent/workers/brd_evidence_loader.py` — new worker
- `tests/test_agent_brd_evidence_loader.py` — tests

## Task 2: Before/After Comparison Worker

New worker `brd.evidence.compare` that wraps `compare_channel_scores`.

- `src/aedt_agent/agent/workers/brd_evidence_compare.py` — new worker
- `tests/test_agent_brd_evidence_compare.py` — tests

## Task 3: Evidence YAML Template

New template `brd_evidence_comparison.yaml`:
  load_before → load_after → score_before → score_after → compare → scorecard

## Task 4: End-to-end test + CLI

CLI `--brd-evidence-compare` flag.
