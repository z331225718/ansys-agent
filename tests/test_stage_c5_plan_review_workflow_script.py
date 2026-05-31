from pathlib import Path


def test_stage_c5_plan_review_workflow_script_reviews_current_plan():
    script = Path("workflows/scripts/stage-c5-plan-review-wf.js")

    content = script.read_text(encoding="utf-8")

    assert "stage-c5-plan-review" in content
    assert "2026-05-31-stage-c5-local-cut-optimization-cell-design.md" in content
    assert "2026-05-31-stage-c5-bbox-local-cut-build.md" in content
    for phase in [
        "Spec-Plan Alignment",
        "Existing-Code Feasibility",
        "PyEDB/PyAEDT API Risk",
        "Local Cut Semantics",
        "Port Selection Risk",
        "Testing Plan Quality",
        "Production Portability",
        "Synthesize",
    ]:
        assert phase in content
    assert content.count("model: 'gpt-5.4'") == 7
    assert content.count("model: 'gpt-5.5'") == 2
    assert "model: 'haiku'" not in content
    assert "model: 'opus'" not in content
    assert "return report;" in content
