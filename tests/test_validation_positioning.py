from __future__ import annotations

from pathlib import Path


REPORT_FILES = [
    Path("docs/aedt-agent-stage-c-progress-report.md"),
    Path("docs/aedt-agent-executive-report.md"),
]


def test_reports_describe_validation_layers_without_overclaiming():
    combined = "\n".join(path.read_text(encoding="utf-8") for path in REPORT_FILES if path.exists())

    assert "结构性验证" in combined
    assert "结果文件验证" in combined
    assert "电磁语义验证" in combined
    assert "不是完整电磁正确性证明" in combined
