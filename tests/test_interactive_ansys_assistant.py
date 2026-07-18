from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from aedt_agent.interactive.catalog import CapabilityCatalog
from aedt_agent.interactive.contracts import PathSelector, RouteKind
from aedt_agent.interactive.kernel import InteractiveKernel
from aedt_agent.interactive.layout import (
    LayoutSessionManager,
    StalePreviewError,
    dimension_to_meters,
    selector_from_payload,
)
from aedt_agent.interactive.router import TaskRouter


class FakeValue(float):
    def __new__(cls, value: float, expression: str):
        instance = super().__new__(cls, value)
        instance.expression = expression
        return instance


class FakeVariable:
    def __init__(self, owner, name: str, value: str, is_parameter: bool) -> None:
        self.owner = owner
        self.name = name
        self.value = value
        self.is_parameter = is_parameter

    def delete(self) -> bool:
        self.owner.design_variables.pop(self.name, None)
        return True


class FakeCell:
    def __init__(self, owner) -> None:
        self.owner = owner

    def add_variable(self, name: str, value: str, is_param: bool = False) -> None:
        self.owner.design_variables[name] = FakeVariable(self.owner, name, value, is_param)


class FakePath:
    primitive_type = "Path"

    def __init__(
        self,
        primitive_id: int,
        width_m: float,
        *,
        net: str,
        layer: str,
        fail_parameterize: bool = False,
    ) -> None:
        self.id = primitive_id
        self.name = f"path_{primitive_id}"
        self.net_name = net
        self.layer_name = layer
        self._width_m = width_m
        self._expression = str(width_m)
        self._parameterized = False
        self._owner = None
        self.fail_parameterize = fail_parameterize

    @property
    def width(self) -> FakeValue:
        return FakeValue(self._width_m, self._expression)

    @width.setter
    def width(self, value) -> None:
        if isinstance(value, str) and self._owner is not None and value in self._owner.design_variables:
            if self.fail_parameterize:
                raise RuntimeError("simulated width setter failure")
            variable = self._owner.design_variables[value]
            self._width_m = dimension_to_meters(variable.value)
            self._expression = value
            self._parameterized = True
            return
        self._width_m = float(value)
        self._expression = str(float(value))
        self._parameterized = False

    def is_parameterized(self) -> bool:
        return self._parameterized


class Polygon:
    primitive_type = "Polygon"

    def __init__(self, primitive_id: int) -> None:
        self.id = primitive_id


class FakeModeler:
    def __init__(self, primitives: list[object]) -> None:
        self.primitives = primitives

    def get_primitives(self, **_filters):
        return list(self.primitives)


class FakeEdb:
    created: list["FakeEdb"] = []
    fail_second_path = False

    def __init__(self, *, edbpath: str, version: str, grpc, isreadonly: bool) -> None:
        self.edbpath = Path(edbpath)
        self.version = version
        self.grpc = grpc
        self.isreadonly = isreadonly
        self.design_variables: dict[str, FakeVariable] = {}
        self.active_cell = FakeCell(self)
        paths = [
            FakePath(11, 0.1e-3, net="DDR_DQ0", layer="L1"),
            FakePath(
                12,
                0.1e-3,
                net="DDR_DQ1",
                layer="L3",
                fail_parameterize=FakeEdb.fail_second_path,
            ),
            FakePath(13, 0.2e-3, net="CLK", layer="L1"),
            Polygon(99),
        ]
        for primitive in paths:
            if isinstance(primitive, FakePath):
                primitive._owner = self
        self.modeler = FakeModeler(paths)
        self.saved = 0
        self.closed = False
        FakeEdb.created.append(self)

    def save(self) -> bool:
        if self.isreadonly:
            raise RuntimeError("cannot save read-only EDB")
        self.saved += 1
        (self.edbpath / "fake-save.txt").write_text(str(self.saved), encoding="ascii")
        return True

    def close(self) -> None:
        self.closed = True


@pytest.fixture(autouse=True)
def reset_fake_edb():
    FakeEdb.created = []
    FakeEdb.fail_second_path = False
    yield
    FakeEdb.fail_second_path = False


@pytest.fixture
def layout_bundle(tmp_path: Path) -> Path:
    project = tmp_path / "board.aedt"
    project.write_text("source-project", encoding="ascii")
    edb = tmp_path / "board.aedb"
    edb.mkdir()
    (edb / "edb.def").write_text("source-edb", encoding="ascii")
    return project


def _kernel() -> InteractiveKernel:
    return InteractiveKernel(session_manager=LayoutSessionManager(edb_factory=FakeEdb))


def test_catalog_and_router_keep_workflows_and_capabilities_separate():
    catalog = CapabilityCatalog()
    router = TaskRouter(catalog, workflow_templates={"brd_channel_optimize"})

    assert router.route(requested_workflow="brd_channel_optimize").kind is RouteKind.WORKFLOW
    route = router.route(requested_capability="layout.paths.list")
    assert route.kind is RouteKind.CAPABILITY
    assert route.target == "layout.paths.list"
    assert router.route(requested_capability="unknown").kind is RouteKind.UNSUPPORTED

    payload = catalog.to_dict()
    assert [item["name"] for item in payload["capabilities"]] == [
        "layout.path_width.parameterize.apply",
        "layout.path_width.parameterize.preview",
        "layout.paths.list",
    ]
    assert payload["capabilities"][0]["risk"] == "reversible_edit"


def test_router_opens_code_fallback_only_when_knowledge_and_policy_are_ready():
    catalog = CapabilityCatalog()
    requested = {"requested_capability": "unknown"}

    assert TaskRouter(catalog, code_fallback_enabled=True).route(**requested).kind is RouteKind.UNSUPPORTED
    assert (
        TaskRouter(
            catalog,
            code_fallback_enabled=True,
            api_memory_ready=True,
        ).route(**requested).kind
        is RouteKind.UNSUPPORTED
    )
    route = TaskRouter(
        catalog,
        code_fallback_enabled=True,
        api_memory_ready=True,
        exploration_policy_enabled=True,
    ).route(**requested)
    assert route.kind is RouteKind.CODE_FALLBACK
    assert route.reason == "capability_miss_with_safe_exploration_ready"

    known = TaskRouter(
        catalog,
        code_fallback_enabled=True,
        api_memory_ready=True,
        exploration_policy_enabled=True,
    ).route(requested_capability="layout.paths.list")
    assert known.kind is RouteKind.CAPABILITY


def test_selector_parses_units_and_filters_paths(layout_bundle: Path):
    kernel = _kernel()
    opened = kernel.open_layout_session(str(layout_bundle), version="2026.1")
    session_id = opened["session_id"]

    result = kernel.execute_capability(
        "layout.paths.list",
        {
            "session_id": session_id,
            "selector": {
                "target_width": "0.1mm",
                "tolerance": "0.1um",
                "nets": ["ddr_dq0"],
                "layers": ["l1"],
            },
        },
    )

    assert result["count"] == 1
    assert result["paths"][0]["primitive_id"] == "11"
    assert result["paths"][0]["width_m"] == pytest.approx(0.1e-3)
    assert kernel.close_layout_session(session_id)["source_unchanged"] is True
    assert FakeEdb.created[0].closed is True


def test_aedt_2024_r2_auto_selects_dotnet_and_rejects_grpc(layout_bundle: Path):
    kernel = _kernel()
    opened = kernel.open_layout_session(
        str(layout_bundle),
        version="2024 R2",
        edb_backend="auto",
    )
    assert opened["version"] == "2024.2"
    assert FakeEdb.created[-1].version == "2024.2"
    assert FakeEdb.created[-1].grpc is False
    kernel.close_layout_session(opened["session_id"])

    with pytest.raises(ValueError, match="gRPC is not supported"):
        kernel.open_layout_session(
            str(layout_bundle),
            version="2024 R2",
            edb_backend="grpc",
        )


def test_parameterize_width_modifies_only_working_copy_and_verifies(layout_bundle: Path, tmp_path: Path):
    source_project = layout_bundle.read_bytes()
    source_edb = layout_bundle.with_suffix(".aedb").joinpath("edb.def").read_bytes()
    kernel = _kernel()
    opened = kernel.open_layout_session(
        str(layout_bundle),
        writable=True,
        workspace=str(tmp_path / "work"),
        edb_backend="grpc",
    )
    session_id = opened["session_id"]

    preview = kernel.execute_capability(
        "layout.path_width.parameterize.preview",
        {
            "session_id": session_id,
            "selector": {
                "target_width": "0.1mm",
                "tolerance": "1nm",
                "parameterized": False,
            },
            "variable_name": "trace_w",
            "variable_value": "0.1mm",
        },
    )
    result = kernel.execute_capability(
        "layout.path_width.parameterize.apply",
        {"session_id": session_id, "preview_id": preview["preview_id"]},
    )

    assert preview["target_count"] == 2
    assert result["status"] == "verified"
    assert result["verified_count"] == 2
    assert {item["width_expression"] for item in result["after"]} == {"trace_w"}
    assert result["evidence"]["variable_is_parameter"] is True
    assert Path(result["working_project_path"]) != layout_bundle
    assert Path(result["working_project_path"]).exists()
    assert layout_bundle.read_bytes() == source_project
    assert layout_bundle.with_suffix(".aedb").joinpath("edb.def").read_bytes() == source_edb
    assert kernel.close_layout_session(session_id)["source_unchanged"] is True


def test_apply_rejects_stale_preview(layout_bundle: Path, tmp_path: Path):
    kernel = _kernel()
    opened = kernel.open_layout_session(str(layout_bundle), writable=True, workspace=str(tmp_path / "work"))
    session_id = opened["session_id"]
    preview = kernel.sessions.preview_parameterize_width(
        session_id,
        selector=PathSelector(target_width_m=0.1e-3, tolerance_m=1e-9),
        variable_name="trace_w",
        variable_value="0.1mm",
    )
    FakeEdb.created[-1].modeler.primitives[0].width = 0.15e-3

    with pytest.raises(StalePreviewError, match="changed after preview"):
        kernel.sessions.apply_parameterize_width(session_id, preview.preview_id)

    kernel.close_layout_session(session_id)


def test_failed_parameterization_rolls_back_widths_and_variable(layout_bundle: Path, tmp_path: Path):
    FakeEdb.fail_second_path = True
    kernel = _kernel()
    opened = kernel.open_layout_session(str(layout_bundle), writable=True, workspace=str(tmp_path / "work"))
    session_id = opened["session_id"]
    preview = kernel.sessions.preview_parameterize_width(
        session_id,
        selector=PathSelector(target_width_m=0.1e-3, tolerance_m=1e-9),
        variable_name="trace_w",
        variable_value="0.1mm",
    )

    with pytest.raises(RuntimeError, match="simulated width setter failure"):
        kernel.sessions.apply_parameterize_width(session_id, preview.preview_id)

    edb = FakeEdb.created[-1]
    assert edb.design_variables == {}
    records = kernel.sessions.list_paths(session_id)["paths"]
    selected = [record for record in records if record["primitive_id"] in {"11", "12"}]
    assert all(record["is_parameterized"] is False for record in selected)
    assert all(record["width_m"] == pytest.approx(0.1e-3) for record in selected)
    kernel.close_layout_session(session_id)


def test_apply_requires_working_copy_session(layout_bundle: Path):
    kernel = _kernel()
    opened = kernel.open_layout_session(str(layout_bundle), writable=False)
    session_id = opened["session_id"]
    preview = kernel.sessions.preview_parameterize_width(
        session_id,
        selector=PathSelector(target_width_m=0.1e-3),
        variable_name="trace_w",
        variable_value="0.1mm",
    )

    with pytest.raises(PermissionError, match="working-copy"):
        kernel.sessions.apply_parameterize_width(session_id, preview.preview_id)

    kernel.close_layout_session(session_id)


def test_capabilities_cli_is_json_and_does_not_open_aedt(capsys):
    from aedt_agent.interactive.cli import main

    assert main(["capabilities"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == "1"
    assert len(payload["capabilities"]) == 3
    assert FakeEdb.created == []


def test_capabilities_v2_is_additive_and_keeps_v1_unchanged(capsys):
    from aedt_agent.interactive.cli import main

    assert main(["capabilities-v2"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["version"] == "2"
    assert payload["compatibility"]["v1_unchanged"] is True
    by_name = {item["name"]: item for item in payload["capabilities"]}
    assert by_name["layout.paths.list"]["modes"] == ["artifact", "live"]
    assert by_name["aedt.projects.save"]["approval"] == "external_host_token"
    assert CapabilityCatalog().to_dict()["version"] == "1"


def test_parameterize_cli_previews_by_default_and_applies_only_with_flag(
    layout_bundle: Path,
    tmp_path: Path,
    monkeypatch,
    capsys,
):
    import aedt_agent.interactive.cli as cli

    monkeypatch.setattr(cli, "InteractiveKernel", lambda: _kernel())
    base_args = [
        "parameterize-width",
        "--project",
        str(layout_bundle),
        "--target-width",
        "0.1mm",
        "--variable-name",
        "trace_w",
        "--workspace",
        str(tmp_path / "cli-work"),
    ]

    assert cli.main(base_args) == 0
    preview_payload = json.loads(capsys.readouterr().out)
    assert preview_payload["status"] == "preview"
    assert preview_payload["preview"]["target_count"] == 2
    assert FakeEdb.created[-1].saved == 0

    assert cli.main([*base_args[:-1], str(tmp_path / "cli-apply"), "--apply"]) == 0
    applied_payload = json.loads(capsys.readouterr().out)
    assert applied_payload["status"] == "verified"
    assert applied_payload["result"]["verified_count"] == 2
    assert applied_payload["close"]["source_unchanged"] is True


def test_mcp_tools_complete_open_preview_apply_close_chain(layout_bundle: Path, tmp_path: Path, monkeypatch):
    class FakeFastMCP:
        def __init__(self, name: str, **kwargs) -> None:
            self.name = name
            self.tools = {}

        def tool(self):
            def register(fn):
                self.tools[fn.__name__] = fn
                return fn

            return register

    monkeypatch.setitem(sys.modules, "fastmcp", SimpleNamespace(FastMCP=FakeFastMCP))
    from aedt_agent.interactive.server import create_server

    server = create_server(kernel=_kernel())
    opened = asyncio.run(
        server.tools["open_layout_session"](
            str(layout_bundle),
            writable=True,
            workspace=str(tmp_path / "mcp-work"),
        )
    )
    session_id = opened["session_id"]
    listed = asyncio.run(
        server.tools["list_layout_paths"](
            session_id,
            {"target_width": "0.1mm", "tolerance": "1nm"},
        )
    )
    preview = asyncio.run(
        server.tools["preview_parameterize_path_width"](
            session_id,
            {"target_width": "0.1mm", "tolerance": "1nm", "parameterized": False},
            "trace_w",
            "0.1mm",
        )
    )
    applied = asyncio.run(
        server.tools["apply_parameterize_path_width"](session_id, preview["preview_id"])
    )
    closed = asyncio.run(server.tools["close_layout_session"](session_id))

    assert listed["count"] == 2
    assert applied["status"] == "verified"
    assert applied["verified_count"] == 2
    assert closed["source_unchanged"] is True


def test_process_layout_timeout_discards_worker_before_next_request(monkeypatch):
    from aedt_agent.interactive.process_manager import ProcessLayoutSessionManager

    class Connection:
        closed = False

        def send(self, payload):
            return None

        def poll(self, timeout):
            return False

        def close(self):
            self.closed = True

    class Process:
        returncode = None
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

        def wait(self, timeout):
            self.returncode = -15
            return self.returncode

    manager = ProcessLayoutSessionManager(timeout_seconds=0)
    connection = Connection()
    process = Process()
    manager._connection = connection
    manager._process = process
    monkeypatch.setattr(manager, "_ensure_started", lambda: None)

    with pytest.raises(TimeoutError, match="timed out"):
        manager._request("open", {})

    assert connection.closed is True
    assert process.terminated is True
    assert manager._connection is None
    assert manager._process is None


def test_process_layout_worker_keeps_ipc_authkey_out_of_command_line(monkeypatch):
    from aedt_agent.interactive import process_manager as process_manager_module

    captured = {}

    class Process:
        returncode = None

        def poll(self):
            return None

    class Connection:
        def close(self):
            return None

    def start(command, **kwargs):
        captured["command"] = command
        captured["environment"] = kwargs["env"]
        return Process()

    monkeypatch.setattr(process_manager_module.subprocess, "Popen", start)
    monkeypatch.setattr(process_manager_module, "Client", lambda *args, **kwargs: Connection())
    manager = process_manager_module.ProcessLayoutSessionManager()

    manager._ensure_started()

    assert "--authkey" not in captured["command"]
    assert len(captured["environment"]["AEDT_AGENT_LAYOUT_WORKER_AUTHKEY"]) == 64
    manager._process = None
    manager._discard_worker()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("0.1mm", 0.1e-3),
        ("4mil", 4 * 25.4e-6),
        ({"value": 100, "unit": "um"}, 100e-6),
        (1e-3, 1e-3),
    ],
)
def test_dimension_parser(value, expected):
    assert dimension_to_meters(value) == pytest.approx(expected)


def test_selector_rejects_unknown_units():
    with pytest.raises(ValueError, match="unsupported dimension unit"):
        selector_from_payload({"target_width": "1inch"})
