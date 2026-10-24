# ADR 0003: Single Ansys DSH mode

## Status

Accepted — 2026-10-24

## Context

M1 T2 introduced separate `ansys-reviewed` and `ansys-explore` DSH modes.
The split added a mode-selection decision to every Ansys task and produced
repeated activation/configuration failures. The user explicitly requires one
mode for Ansys work.

## Decision

Ship one `ansys-engineering` mode containing the official PyAEDT-MCP,
ansys-assistant, ansys-api-memory, shell/filesystem and PTC.

A small `production-guard.mjs` plugin denies only the production graph
lifecycle tools:

- preview/apply workflow start
- preview/apply workflow advance

Mission creation, advance and human_gate approval remain in the controlled
local CLI/dashboard. Geometry preview/apply and normal exploration stay
available in the single mode.

## Consequences

- Reviewed/explore preset assets and reviewed MCP server mode are removed.
- Read-only Mission observation remains available in the dashboard/workbench,
  not as a DSH mode.
- The installer manages one local bundle instead of inline preset rows.
