"""Offline regression tests for the AI-HFSS single-point solve node.

Every test here runs without an AEDT session: the real 4-port export captured
from the reviewed layout is the fixture, and the AEDT calls are faked. The three
defects pinned down are the hard-coded `.s1p` name, the one-frequency-per-line
Touchstone parser, and reading `len(list[TouchstoneData])` as a point count.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from aedt_agent.agent import aihfss_smoke
from aedt_agent.agent.aihfss_smoke import (
    _design_port_count,
    _parse_touchstone,
    _touchstone_frequencies,
    _touchstone_point_count,
    _touchstone_points_from_data,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "touchstone"
REAL_S4P = FIXTURES / "channel_1ghz.s4p"
BUGGY_PAYLOAD = FIXTURES / "solve_point_result_s1p_bug.json"


# ── defect 2: Touchstone read-back ──────────────────────────────────────────


def test_real_four_port_export_reads_one_point_and_four_ports():
    """The real HFSS 3D Layout export: one frequency point, four ports."""
    parsed = _parse_touchstone(REAL_S4P)
    assert parsed["point_count"] == 1
    assert parsed["frequencies_ghz"] == [1.0]
    assert parsed["port_count"] == 4
    assert parsed["data_format"] == "MA"
    assert parsed["frequency_unit"] == "GHZ"
    assert parsed["warnings"] == []


def test_legacy_helpers_agree_with_the_real_export():
    """`_touchstone_point_count` / `_touchstone_frequencies` no longer read S values."""
    assert _touchstone_point_count(REAL_S4P) == 1
    assert _touchstone_frequencies(REAL_S4P) == [1.0]


def test_buggy_payload_fixture_shows_what_the_old_parser_returned():
    """Guard the regression: the captured payload held S values as frequencies."""
    payload = json.loads(BUGGY_PAYLOAD.read_text(encoding="utf-8"))
    assert payload["touchstone_frequencies_ghz"] == [1.0, 0.008451, 0.998905, 0.002847]
    assert payload["touchstone_path"].endswith(".s1p"), "the old name was hard-coded"
    assert _touchstone_frequencies(REAL_S4P) != payload["touchstone_frequencies_ghz"]


def test_ri_format_multi_point_two_port(tmp_path: Path):
    """RI data, 2 ports, 3 points: each point is 9 numbers."""
    path = tmp_path / "line.s2p"
    path.write_text(
        "! comment\n"
        "# Hz S RI R 50\n"
        "1e9 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8\n"
        "2e9 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8\n"
        "3e9 0.1 0.2 0.3 0.4 0.5 0.6 0.7 0.8\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["point_count"] == 3
    assert parsed["port_count"] == 2
    assert parsed["frequencies_ghz"] == [1.0, 2.0, 3.0]
    assert parsed["data_format"] == "RI"
    assert parsed["warnings"] == []


def test_db_format_with_mhz_unit(tmp_path: Path):
    """DB data in MHz is still sliced by port count, not by line."""
    path = tmp_path / "amp.s2p"
    path.write_text(
        "# MHz S DB R 50\n"
        "1000 -20 30 -40 60 -40 60 -20 30\n"
        "2000 -21 31 -41 61 -41 61 -21 31\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["point_count"] == 2
    assert parsed["frequencies_ghz"] == [1.0, 2.0]
    assert parsed["data_format"] == "DB"
    assert parsed["frequency_unit"] == "MHZ"


def test_option_line_without_a_unit_keeps_the_default(tmp_path: Path):
    """`# S MA R 50` names no unit; the default GHz must not become "S"."""
    path = tmp_path / "nounit.s2p"
    path.write_text(
        "# S MA R 50\n"
        "1 0.1 0 0.2 0 0.3 0 0.4 0\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["frequency_unit"] == "GHZ"
    assert parsed["frequencies_ghz"] == [1.0]
    assert parsed["data_format"] == "MA"


def test_point_split_across_lines_is_reassembled(tmp_path: Path):
    """A writer may break a point over rows; the stride, not the row, counts."""
    path = tmp_path / "wrapped.s2p"
    path.write_text(
        "# GHz S MA R 50\n"
        "1 0.1 0 0.2 0\n"
        "  0.3 0 0.4 0\n"
        "2 0.1 0 0.2 0\n"
        "  0.3 0 0.4 0\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["point_count"] == 2
    assert parsed["frequencies_ghz"] == [1.0, 2.0]
    assert parsed["warnings"] == []


def test_truncated_file_is_bounded_and_flagged(tmp_path: Path):
    """A whole point is kept; a trailing half point is dropped with a warning."""
    path = tmp_path / "cut.s2p"
    path.write_text(
        "# GHz S MA R 50\n"
        "1 0.1 0 0.2 0 0.3 0 0.4 0\n"
        "  2 0.1 0 0.2 0\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["point_count"] == 1
    assert parsed["frequencies_ghz"] == [1.0]
    assert parsed["port_count"] == 2
    assert any("truncated" in warning for warning in parsed["warnings"])


def test_comment_only_file_yields_zero_points(tmp_path: Path):
    path = tmp_path / "empty.s2p"
    path.write_text(
        "! Touchstone file exported from HFSS 3D Layout Design\n"
        "! Port[1] = a\n"
        "! Port[2] = b\n"
        "# GHz S MA R 50\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["point_count"] == 0
    assert parsed["frequencies_ghz"] == []
    assert parsed["port_count"] == 2
    assert any("no data rows" in warning for warning in parsed["warnings"])
    assert _touchstone_point_count(path) == 0
    assert _touchstone_frequencies(path) == []


def test_missing_file_yields_zero_points(tmp_path: Path):
    parsed = _parse_touchstone(tmp_path / "absent.s2p")
    assert parsed["point_count"] == 0
    assert parsed["port_count"] == 0
    assert parsed["warnings"] == ["touchstone file is missing"]


def test_suffix_and_port_declarations_disagree_is_bounded(tmp_path: Path):
    """A `.s4p` carrying two `! Port[n]` lines is ambiguous: report, do not guess."""
    path = tmp_path / "mismatch.s4p"
    path.write_text(
        "! Port[1] = a\n"
        "! Port[2] = b\n"
        "# GHz S MA R 50\n"
        "1 0.1 0 0.2 0 0.3 0 0.4 0\n"
        "  0.1 0 0.2 0 0.3 0 0.4 0\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["point_count"] == 0
    assert parsed["frequencies_ghz"] == []
    assert parsed["port_count"] == 2
    assert any("refusing to parse" in warning for warning in parsed["warnings"])


def test_port_count_falls_back_to_declarations_without_a_suffix(tmp_path: Path):
    """A `.txt` export still parses when `! Port[n]` lines name the width."""
    path = tmp_path / "export.txt"
    path.write_text(
        "! Port[1] = a\n! Port[2] = b\n# GHz S MA R 50\n"
        "1 0.1 0 0.2 0 0.3 0 0.4 0\n",
        encoding="utf-8",
    )
    parsed = _parse_touchstone(path)
    assert parsed["port_count"] == 2
    assert parsed["point_count"] == 1
    assert parsed["frequencies_ghz"] == [1.0]


def test_unknown_port_count_is_bounded(tmp_path: Path):
    path = tmp_path / "nameless.dat"
    path.write_text("# GHz S MA R 50\n1 0.1 0 0.2 0 0.3 0 0.4 0\n", encoding="utf-8")
    parsed = _parse_touchstone(path)
    assert parsed["point_count"] == 0
    assert any("port count is unknown" in warning for warning in parsed["warnings"])


# ── defect 3: get_touchstone_data return value ──────────────────────────────


class _FakeNetwork:
    def __init__(self, points: int) -> None:
        self.f = [float(index) * 1e9 for index in range(points)]


class _FakeTouchstoneData:
    """Mirrors pyaedt's TouchstoneData: a skrf Network with an `f` array."""

    def __init__(self, points: int) -> None:
        self.f = _FakeNetwork(points).f

    def __len__(self) -> int:
        return 4  # nports, not points — the trap the old code fell into


def test_touchstone_points_come_from_the_frequency_axis():
    data = [_FakeTouchstoneData(1)]
    assert len(data) == 1
    assert _touchstone_points_from_data(data) == 1


def test_touchstone_points_are_not_the_variation_count():
    """Two variations of 7 points is 7 points, not 2."""
    data = [_FakeTouchstoneData(7), _FakeTouchstoneData(7)]
    assert _touchstone_points_from_data(data) == 7


def test_touchstone_points_handle_odd_returns():
    assert _touchstone_points_from_data(None) == 0
    assert _touchstone_points_from_data([]) == 0
    assert _touchstone_points_from_data([object()]) == 0
    assert _touchstone_points_from_data(_FakeTouchstoneData(3)) == 3


# ── defect 1: export name follows the real port count ───────────────────────


class _FakeSweep:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeSetup:
    def __init__(self, sweeps: list[str]) -> None:
        self.sweeps = [_FakeSweep(name) for name in sweeps]


class _FakeLayout:
    """The narrow slice of Hfss3dLayout the solve node touches."""

    def __init__(
        self,
        *,
        ports: list[str],
        touchstone_source: Path,
        export_as: str,
        points: int = 1,
        data_raises: bool = False,
    ) -> None:
        self.design_name = "fake_design"
        self._ports = ports
        self._source = touchstone_source
        self._export_as = export_as
        self._points = points
        self._data_raises = data_raises
        self.exported_to: Path | None = None

    @property
    def port_list(self) -> list[str]:
        return list(self._ports)

    def get_setup(self, name: str) -> _FakeSetup:
        return _FakeSetup(["AIHFSS_1GHz"])

    def analyze_setup(self, name: str) -> bool:
        return True

    def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
        target = Path(output_file)
        self.exported_to = target
        text = self._source.read_text(encoding="utf-8")
        if self._export_as != target.name:
            text = text.replace("! Port[4] = ", "! Port[2] = ")
        target.write_text(text, encoding="utf-8")
        return str(target)

    def get_touchstone_data(self, *, setup: str, sweep: str):
        if self._data_raises:
            raise RuntimeError("AEDT could not build the solution data")
        return [_FakeTouchstoneData(self._points)]


def _run_node(tmp_path: Path, layout: _FakeLayout, payload: dict) -> dict:
    """Run the solve node against a fake Hfss3dLayout, with no AEDT import."""
    source = tmp_path / "source.aedt"
    source.write_text("fake project", encoding="utf-8")
    request = {
        "artifact_dir": str(tmp_path / "artifacts"),
        "source_project_path": str(source),
        "working_project_path": str(tmp_path / "working" / "source.aedt"),
        "frequency_ghz": 1.0,
        **payload,
    }

    class _Context:
        input_payload = request

    class _Desktop:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def load_project(self, path: str) -> bool:
            return True

        def release_desktop(self, **kwargs) -> None:
            pass

    def _layout_factory(*args, **kwargs) -> _FakeLayout:
        return layout

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setitem(
        __import__("sys").modules,
        "ansys.aedt.core",
        type(
            "FakeAedtCore",
            (),
            {"Desktop": _Desktop, "Hfss3dLayout": _layout_factory},
        ),
    )
    try:
        result = aihfss_smoke._solve_layout_point(_Context())
    finally:
        monkeypatch.undo()
    return result


def test_four_port_design_exports_s4p_by_default(tmp_path: Path):
    layout = _FakeLayout(
        ports=["Port1", "Port2", "Port3", "Port4"],
        touchstone_source=REAL_S4P,
        export_as="channel_1ghz.s4p",
    )
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert layout.exported_to is not None
    assert layout.exported_to.name == "channel_1ghz.s4p"
    assert payload["touchstone_kind"] == "s4p"
    assert payload["touchstone_port_count"] == 4
    assert payload["touchstone_points"] == 1
    assert payload["touchstone_frequencies_ghz"] == [1.0]
    assert payload["touchstone_parse_warnings"] == []
    written = json.loads((tmp_path / "artifacts" / "solve_point_result.json").read_text(encoding="utf-8"))
    assert written["touchstone_kind"] == "s4p"


def test_single_port_design_still_exports_s1p(tmp_path: Path):
    single = tmp_path / "one.s1p"
    single.write_text("# GHz S MA R 50\n1 0.5 10\n", encoding="utf-8")
    layout = _FakeLayout(
        ports=["Port1"],
        touchstone_source=single,
        export_as="channel_1ghz.s1p",
    )
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert layout.exported_to is not None
    assert layout.exported_to.name == "channel_1ghz.s1p"
    assert payload["touchstone_kind"] == "s1p"
    assert payload["touchstone_port_count"] == 1
    assert payload["touchstone_points"] == 1


def test_explicit_touchstone_name_wins_and_mismatch_is_flagged(tmp_path: Path):
    """A user name is honoured, but a wrong suffix is reported, not hidden."""
    layout = _FakeLayout(
        ports=["Port1", "Port2", "Port3", "Port4"],
        touchstone_source=REAL_S4P,
        export_as="custom.s1p",
    )
    result = _run_node(tmp_path, layout, {"touchstone_name": "custom.s1p"})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert layout.exported_to is not None
    assert layout.exported_to.name == "custom.s1p"
    assert payload["touchstone_kind"] == "s4p"
    assert payload["touchstone_port_count"] == 4
    assert any("does not match" in warning for warning in payload["touchstone_parse_warnings"])


def test_port_list_failure_falls_back_to_the_file_width(tmp_path: Path):
    """If AEDT cannot report ports, the export still gets a file and is parsed."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    layout = _NoPorts(
        ports=[],
        touchstone_source=REAL_S4P,
        export_as="channel_1ghz.s1p",
    )
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    # No port count is known before the export, so the name keeps the old
    # single-port default; the parsed file then reports the true width.
    assert layout.exported_to is not None
    assert layout.exported_to.name == "channel_1ghz.s1p"
    assert payload["touchstone_port_count"] == 4
    assert payload["touchstone_kind"] == "s4p"
    assert payload["touchstone_points"] == 1


def test_get_touchstone_data_failure_falls_back_to_the_file(tmp_path: Path):
    layout = _FakeLayout(
        ports=["Port1", "Port2", "Port3", "Port4"],
        touchstone_source=REAL_S4P,
        export_as="channel_1ghz.s4p",
        data_raises=True,
    )
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert payload["touchstone_points"] == 1
    assert payload["touchstone_frequencies_ghz"] == [1.0]


def test_design_port_count_is_bounded():
    class _Boom:
        @property
        def port_list(self):
            raise RuntimeError("no AEDT")

    class _List:
        port_list = ["a", "b", "c", "d"]

    assert _design_port_count(_Boom()) == 0
    assert _design_port_count(_List()) == 4
    assert _design_port_count(object()) == 0
