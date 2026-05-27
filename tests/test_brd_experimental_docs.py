from pathlib import Path


def test_brd_experimental_docs_define_unified_artifacts():
    text = Path("docs/brd-experimental-workflow.md").read_text(encoding="utf-8")

    assert "workflow_run.json" in text
    assert "import_cutout_summary.json" in text
    assert "acceptance_report.json" in text
    assert "acceptance_report.html" in text
    assert "preflight.json" in text
    assert "params.json" in text
    assert "model-build only" in text
    assert "model-build-only" in text
    assert "不运行 analyze" in text
    assert "默认不 analyze" in text
