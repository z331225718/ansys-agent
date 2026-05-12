# Stage B Validation Script Specification

Validation scripts are introduced in Stage A as contracts and become executable checks in Stage B.

## Interface

Each validation script must expose:

```python
def validate(session_id, project_id, design_id) -> dict:
    ...
```

The returned dictionary must contain at least:

- `passed`: boolean overall result
- `checks`: list of individual check names or summaries

Optional fields:

- `details`: structured evidence for each check
- `artifacts`: output paths or exported files used during validation

## Rules

- Each validation script must include at least one `assert` per intended check.
- Stage A may use mock implementations or placeholders because AEDT is not connected.
- Trap tasks must explicitly validate silent-failure scenarios rather than only crash conditions.
- Validation scripts must not modify AEDT state beyond read-only inspection.
- Validation logic should be deterministic for the same `(session_id, project_id, design_id)` input.

## Stage A Usage

Stage A stores these script paths in benchmark metadata so the contract is available before live execution exists.

## Stage B Expectation

Stage B will call `validate(session_id, project_id, design_id)` after node execution and merge the result into benchmark reporting.
