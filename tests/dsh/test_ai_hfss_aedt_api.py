from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHAT_SCRIPT = REPO / "integrations" / "aedt" / "ai_hfss_chat.py"


def _run_node(script_name: str, marker: str) -> None:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is required for the AI-HFSS DSH tests")
    script = Path(__file__).with_name(script_name)
    result = subprocess.run([node, str(script)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr or result.stdout
    assert marker in result.stdout


def test_aedt_handoff_api_routes_and_session():
    """Mount the real host half and drive /api/ai-hfss/aedt/context|status|open."""
    _run_node("ai_hfss_aedt_api_test.mjs", "ai-hfss aedt api test passed")


def test_focus_mode_client_contract():
    """Focus mode collapses the sidebar, renders the status strip, and skips slots."""
    _run_node("ai_hfss_focus_test.mjs", "ai-hfss focus test passed")


def _load_chat_script():
    spec = importlib.util.spec_from_file_location("ai_hfss_chat_under_test", CHAT_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class _CaptureHandler(BaseHTTPRequestHandler):
    captured: dict = {}

    def do_POST(self):  # noqa: N802 - http.server API
        length = int(self.headers.get("content-length", "0"))
        self.captured["path"] = self.path
        self.captured["header"] = self.headers.get("x-ai-hfss")
        self.captured["body"] = json.loads(self.rfile.read(length).decode("utf-8"))
        payload = json.dumps(
            {
                "ok": True,
                "session_id": "sess-script",
                "focus_url": "http://127.0.0.1:9999/?aihfss=focus&session=sess-script",
            }
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.send_header("content-length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *args):  # keep the test output clean
        pass


def test_aedt_script_posts_the_project_context(monkeypatch):
    """The AEDT button script reports the project and requests the focus URL."""
    module = _load_chat_script()
    _CaptureHandler.captured = {}
    server = ThreadingHTTPServer(("127.0.0.1", 0), _CaptureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        monkeypatch.setenv("AIHFSS_DSH_PORT", str(server.server_address[1]))
        monkeypatch.setenv("AIHFSS_NO_GUI", "1")
        monkeypatch.setenv("AEDT_PROJECT", "C:\\proj\\demo.aedt")
        monkeypatch.setenv("AEDT_DESIGN", "HFSSDesign1")
        monkeypatch.setattr(sys, "argv", [str(CHAT_SCRIPT)])
        opened = []
        monkeypatch.setattr(module, "_open_url", lambda url: opened.append(url) or "manual")

        assert module.main() == 0

        assert _CaptureHandler.captured["path"] == "/api/ai-hfss/aedt/open"
        assert _CaptureHandler.captured["header"] == "1", "the host guard needs the x-ai-hfss header"
        body = _CaptureHandler.captured["body"]
        assert body["project_path"] == "C:\\proj\\demo.aedt"
        assert body["design_name"] == "HFSSDesign1"
        assert opened == ["http://127.0.0.1:9999/?aihfss=focus&session=sess-script"]
    finally:
        server.shutdown()
        server.server_close()


def test_ansys_embedded_preset_is_light_and_carries_the_aedt_contract():
    """The embedded preset adds MCP + skills but no DAG, goal, or guard tools.

    PyYAML's safe_load cannot read this file: the DSH loader's !!js tags are not
    part of YAML 1.2 (the same tag is already used by every existing row), so
    the test registers a scalar constructor for it and then asserts the
    structure the cordis loader consumes.
    """
    yaml = pytest.importorskip("yaml")
    patch = REPO / "integrations" / "dsh" / "bundles" / "ansys-engineering" / "cordis.patch.yml"
    text = patch.read_text(encoding="utf-8")

    class _JsLoader(yaml.SafeLoader):
        pass

    _JsLoader.add_constructor("tag:yaml.org,2002:js", lambda loader, node: loader.construct_scalar(node))
    insert = yaml.load(text, Loader=_JsLoader)[0]["insert"]
    rows = [row["id"] for row in insert]
    assert rows.count("preset-ansys-engineering") == 1, "the existing preset stays untouched"
    assert rows.count("preset-ansys-embedded") == 1

    config = next(row for row in insert if row["id"] == "preset-ansys-embedded")["config"]
    assert config["id"] == "ansys-embedded"
    assert config["name"] == "AEDT 内嵌助手"
    assert config["description"] == "AEDT 内嵌轻对话：MCP 工具 + 优化 skill，不加载 DAG/审批链"

    plugins = {plugin["id"]: plugin for plugin in config["plugins"]}
    for required in (
        "persona",
        "tool-pwsh",
        "tool-fs",
        "tool-fs-search",
        "tool-jobs",
        "skill-filesystem",
        "tool-skill",
        "tool-web",
        "tool-ask-user",
        "tool-todo",
        "tool-presentation",
    ):
        assert required in plugins, required
    for forbidden in ("tool-goal", "production-guard", "plan-mode", "tool-workflow", "workflow-ptc", "tool-ralph"):
        assert forbidden not in plugins, forbidden

    persona = plugins["persona"]["config"]["prefix"]
    for needle in (
        "mcp__aedt__check_aedt_status",
        "mcp__aedt__connect_to_aedt",
        "SDD11",
        "TDR",
        "preview",
        "{{model}}",
        "{{cwd}}",
    ):
        assert needle in persona, needle

    skill_dirs = plugins["skill-filesystem"]["config"]["customSkillDirs"]
    assert skill_dirs == [
        "'__REPO_ROOT__/integrations/dsh/agent-presets/ansys-engineering/skills'"
    ], skill_dirs


def test_aedt_script_fails_loudly_without_a_project(monkeypatch, capsys):
    """No project anywhere: non-zero exit plus a manual command, never silence."""
    module = _load_chat_script()
    monkeypatch.setenv("AIHFSS_NO_GUI", "1")
    monkeypatch.delenv("AEDT_PROJECT", raising=False)
    monkeypatch.delenv("AEDT_DESIGN", raising=False)
    monkeypatch.delenv("AIHFSS_DSH_PORT", raising=False)
    monkeypatch.delenv("DSH_HOST_PORT", raising=False)
    monkeypatch.setattr(sys, "argv", [str(CHAT_SCRIPT)])

    assert module.main() == 1

    output = capsys.readouterr().out
    assert "无法从 AEDT 读取当前工程" in output
    assert "Invoke-RestMethod" in output, "the failure carries a manual command"
