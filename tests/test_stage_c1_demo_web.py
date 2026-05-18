from pathlib import Path

from aedt_agent.demo.service import DemoService
from aedt_agent.demo.web import dispatch_demo_request, render_demo_page


def test_render_demo_page_contains_workspace_sections():
    html = render_demo_page()

    assert "AEDT Agent Stage C.1" in html
    assert "Templates" in html
    assert "Workflow Preview" in html
    assert "Run Fake Demo" in html


def test_dispatch_demo_request_serves_api_json(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "run")

    status, headers, body = dispatch_demo_request("GET", "/api/templates", b"", service)

    assert status == 200
    assert headers["content-type"] == "application/json; charset=utf-8"
    assert b"microstrip_sparameter" in body


def test_dispatch_demo_request_serves_report_html(tmp_path):
    service = DemoService(Path("."), run_dir=tmp_path / "run")

    status, headers, body = dispatch_demo_request("GET", "/reports/stage_c_real_smoke_dashboard.html", b"", service)

    assert status == 200
    assert headers["content-type"] == "text/html; charset=utf-8"
    assert "Stage C".encode() in body


def test_stage_c1_demo_start_script_exists():
    script = Path("scripts/run_stage_c1_demo_server.py")

    assert script.exists()
    assert "run_demo_server" in script.read_text(encoding="utf-8")
