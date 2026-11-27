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
    _touchstone_nports_from_data,
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
    """Mirrors pyaedt's TouchstoneData: a skrf Network with `f` and `nports`."""

    def __init__(self, points: int, nports: int = 4) -> None:
        self.f = _FakeNetwork(points).f
        self.nports = nports

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


def _one_point_touchstone(port_count: int) -> str:
    """A one-point Touchstone body declaring `port_count` ports."""
    ports = "".join(f"! Port[{index}] = p{index}\n" for index in range(1, port_count + 1))
    values = " ".join("0.1 0" for _ in range(port_count * port_count))
    return f"{ports}# GHz S MA R 50\n1 {values}\n"


class _FakeLayout:
    """The narrow slice of Hfss3dLayout the solve node touches."""

    def __init__(
        self,
        *,
        ports: list[str],
        file_port_count: int,
        points: int = 1,
        data_raises: bool = False,
    ) -> None:
        self.design_name = "fake_design"
        self._ports = ports
        self._file_port_count = file_port_count
        self._points = points
        self._data_raises = data_raises
        self.exported_to: Path | None = None
        self.export_calls: list[Path] = []

    @property
    def port_list(self) -> list[str]:
        return list(self._ports)

    def get_setup(self, name: str) -> _FakeSetup:
        return _FakeSetup(["AIHFSS_1GHz"])

    def analyze_setup(self, name: str) -> bool:
        return True

    def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
        """Write whatever the requested name is — AEDT honours the path given."""
        target = Path(output_file)
        self.exported_to = target
        self.export_calls.append(target)
        if self._file_port_count == 4:
            text = REAL_S4P.read_text(encoding="utf-8")
        else:
            text = _one_point_touchstone(self._file_port_count)
        target.write_text(text, encoding="utf-8")
        return str(target)

    def get_touchstone_data(self, *, setup: str, sweep: str):
        if self._data_raises:
            raise RuntimeError("AEDT could not build the solution data")
        return [_FakeTouchstoneData(self._points, self._file_port_count)]


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
    layout = _FakeLayout(ports=["Port1", "Port2", "Port3", "Port4"], file_port_count=4)
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
    assert payload["touchstone_renamed_from"] == ""
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.s4p"
    written = json.loads((tmp_path / "artifacts" / "solve_point_result.json").read_text(encoding="utf-8"))
    assert written["touchstone_kind"] == "s4p"


def test_single_port_design_still_exports_s1p(tmp_path: Path):
    layout = _FakeLayout(ports=["Port1"], file_port_count=1)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert layout.exported_to is not None
    assert layout.exported_to.name == "channel_1ghz.s1p"
    assert payload["touchstone_kind"] == "s1p"
    assert payload["touchstone_port_count"] == 1
    assert payload["touchstone_points"] == 1
    assert payload["touchstone_renamed_from"] == ""


def test_explicit_touchstone_name_wins_and_mismatch_is_flagged(tmp_path: Path):
    """A user name is honoured, but a wrong suffix is reported, not hidden."""
    layout = _FakeLayout(ports=["Port1", "Port2", "Port3", "Port4"], file_port_count=4)
    result = _run_node(tmp_path, layout, {"touchstone_name": "custom.s1p"})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert layout.exported_to is not None
    assert layout.exported_to.name == "custom.s1p"
    # The user's name is never rewritten, even though the suffix is wrong.
    assert (tmp_path / "artifacts" / "custom.s1p").is_file()
    assert not (tmp_path / "artifacts" / "custom.s4p").exists()
    assert Path(payload["touchstone_path"]).name == "custom.s1p"
    assert payload["touchstone_renamed_from"] == ""
    assert payload["touchstone_kind"] == "s4p"
    assert payload["touchstone_port_count"] == 4
    assert any("does not match" in warning for warning in payload["touchstone_parse_warnings"])


def test_port_list_failure_still_lands_on_the_real_port_count(tmp_path: Path):
    """`port_list` raising must not leave a 4-port export named `.s1p`.

    Downstream (`brd_channel_score`) picks differential mode from the file
    suffix, so a wrong suffix would silently downgrade a differential channel to
    single-ended. The export goes to a provisional name and is renamed once the
    file declares its width.
    """

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    layout = _NoPorts(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    artifacts = tmp_path / "artifacts"
    assert layout.exported_to is not None
    # AEDT wrote the provisional name; the node then renamed it.
    assert layout.exported_to.name == "channel_1ghz.touchstone"
    assert not (artifacts / "channel_1ghz.touchstone").exists()
    assert (artifacts / "channel_1ghz.s4p").is_file()
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.s4p"
    assert payload["touchstone_renamed_from"] == "channel_1ghz.touchstone"
    assert payload["touchstone_port_count"] == 4
    assert payload["touchstone_kind"] == "s4p"
    assert payload["touchstone_points"] == 1
    assert payload["touchstone_parse_warnings"] == []


def test_port_list_failure_on_a_single_port_design_lands_on_s1p(tmp_path: Path):
    """The rename follows the file, not a guess: a 1-port export becomes `.s1p`."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    layout = _NoPorts(ports=[], file_port_count=1)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert (tmp_path / "artifacts" / "channel_1ghz.s1p").is_file()
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.s1p"
    assert payload["touchstone_kind"] == "s1p"
    assert payload["touchstone_port_count"] == 1


def test_rename_replaces_a_stale_file_from_an_earlier_run(tmp_path: Path):
    """A leftover `.s4p` from a previous round is overwritten, not duplicated."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "channel_1ghz.s4p").write_text("stale", encoding="utf-8")
    layout = _NoPorts(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    text = (artifacts / "channel_1ghz.s4p").read_text(encoding="utf-8")
    assert "stale" not in text
    assert "! Port[4]" in text


def test_unknown_width_keeps_the_provisional_name_with_a_warning(tmp_path: Path):
    """No port list and no readable width: keep the placeholder, say why."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    class _Blank(_NoPorts):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            target = Path(output_file)
            self.exported_to = target
            target.write_text("# GHz S MA R 50\n", encoding="utf-8")
            return str(target)

        def get_touchstone_data(self, *, setup: str, sweep: str):
            raise RuntimeError("no solution data either")

    layout = _Blank(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.touchstone"
    assert (tmp_path / "artifacts" / "channel_1ghz.touchstone").is_file()
    assert payload["touchstone_kind"] == "unknown"
    assert payload["touchstone_port_count"] == 0
    assert payload["touchstone_renamed_from"] == ""
    assert any("width is unknown" in warning for warning in payload["touchstone_parse_warnings"])


def test_port_count_falls_back_to_the_touchstone_data(tmp_path: Path):
    """A file with no width declarations is still named from TouchstoneData.nports."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    class _Undeclared(_NoPorts):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            target = Path(output_file)
            self.exported_to = target
            # Valid 4-port data, but no `! Port[n]` lines and no usable suffix.
            target.write_text(
                "# GHz S MA R 50\n1 " + " ".join(["0.1 0"] * 16) + "\n",
                encoding="utf-8",
            )
            return str(target)

    layout = _Undeclared(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.s4p"
    assert payload["touchstone_port_count"] == 4
    assert payload["touchstone_kind"] == "s4p"


def test_extension_appended_by_aedt_is_adopted(tmp_path: Path):
    """AEDT may append its own `.sNp` to a name it does not recognise."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    class _AppendsSuffix(_NoPorts):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            target = Path(output_file).with_suffix(".s4p")
            self.exported_to = target
            self.export_calls.append(target)
            target.write_text(REAL_S4P.read_text(encoding="utf-8"), encoding="utf-8")
            return str(target)

    layout = _AppendsSuffix(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    # Already correctly named by AEDT: nothing to rename, nothing to warn about.
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.s4p"
    assert payload["touchstone_exists"] is True
    assert payload["touchstone_kind"] == "s4p"
    assert payload["touchstone_renamed_from"] == ""
    assert payload["touchstone_parse_warnings"] == []


def test_missing_export_file_is_reported_not_disguised(tmp_path: Path):
    """An export that writes nothing must say so; no rename can invent a file."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    class _WritesNothing(_NoPorts):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            self.exported_to = Path(output_file)
            return str(output_file)

    layout = _WritesNothing(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert payload["touchstone_exists"] is False
    # No file means no file-derived fact: reporting `s4p`/`points: 1` here would
    # describe a file this round never wrote.
    assert payload["touchstone_kind"] == "unknown"
    assert payload["touchstone_port_count"] == 0
    assert payload["touchstone_points"] == 0
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.touchstone"
    assert payload["touchstone_renamed_from"] == ""
    assert any(
        "no file was found" in warning for warning in payload["touchstone_parse_warnings"]
    )


def test_missing_export_file_without_solution_data_reports_unknown(tmp_path: Path):
    """Neither the file nor the solution data can name the width: say unknown."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    class _WritesNothing(_NoPorts):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            self.exported_to = Path(output_file)
            return str(output_file)

        def get_touchstone_data(self, *, setup: str, sweep: str):
            raise RuntimeError("no solution data")

    layout = _WritesNothing(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert payload["touchstone_exists"] is False
    assert payload["touchstone_kind"] == "unknown"
    assert payload["touchstone_port_count"] == 0
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.touchstone"
    warnings = payload["touchstone_parse_warnings"]
    assert any("no file was found" in warning for warning in warnings)


def test_leftover_export_from_an_earlier_round_is_never_adopted(tmp_path: Path):
    """A reused artifact dir must not hand back the previous round's file.

    Round 1 leaves `channel_1ghz.s4p`. Round 2 cannot report ports and its export
    writes nothing. Without clearing, the glob would find round 1's file and the
    node would report `s4p` / `points: 1` for a file it never wrote.
    """

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    class _WritesNothing(_NoPorts):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            self.exported_to = Path(output_file)
            return str(output_file)

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    leftover = artifacts / "channel_1ghz.s4p"
    leftover.write_text(REAL_S4P.read_text(encoding="utf-8"), encoding="utf-8")

    layout = _WritesNothing(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert payload["touchstone_exists"] is False
    assert payload["touchstone_kind"] == "unknown"
    assert payload["touchstone_port_count"] == 0
    assert payload["touchstone_points"] == 0
    assert Path(payload["touchstone_path"]).name == "channel_1ghz.touchstone"
    assert payload["touchstone_renamed_from"] == ""
    warnings = payload["touchstone_parse_warnings"]
    assert any("no file was found" in warning for warning in warnings)
    # The earlier round's file is cleared before the export, so it cannot be
    # mistaken for this round's output either.
    assert not leftover.exists()


def test_leftover_is_cleared_then_the_new_export_is_adopted(tmp_path: Path):
    """Same setup, but AEDT writes this time: the fresh file is claimed cleanly."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "channel_1ghz.s4p").write_text("stale round-1 content", encoding="utf-8")

    layout = _NoPorts(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert payload["touchstone_exists"] is True
    assert payload["touchstone_kind"] == "s4p"
    assert payload["touchstone_port_count"] == 4
    assert payload["touchstone_points"] == 1
    assert payload["touchstone_parse_warnings"] == []
    assert payload["touchstone_renamed_from"] == "channel_1ghz.touchstone"
    final = artifacts / "channel_1ghz.s4p"
    assert final.is_file()
    assert "stale round-1 content" not in final.read_text(encoding="utf-8")
    assert "! Port[4]" in final.read_text(encoding="utf-8")


def test_clearing_leaves_other_stems_and_files_alone(tmp_path: Path):
    """Only this frequency's own exports are cleared."""

    class _NoPorts(_FakeLayout):
        @property
        def port_list(self) -> list[str]:
            raise RuntimeError("GetAllPortsList failed")

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    neighbours = {
        "channel_2ghz.s2p": "# GHz S MA R 50\n1 0.1 0 0.2 0 0.3 0 0.4 0\n",
        "other.s4p": REAL_S4P.read_text(encoding="utf-8"),
        "channel_1ghz.png": "not a touchstone",
        "channel_1ghz_notes.txt": "keep me",
    }
    for name, text in neighbours.items():
        (artifacts / name).write_text(text, encoding="utf-8")
    (artifacts / "channel_1ghz.s1p").write_text("stale", encoding="utf-8")

    layout = _NoPorts(ports=[], file_port_count=4)
    result = _run_node(tmp_path, layout, {})
    assert result["status"] == "succeeded", result
    for name in neighbours:
        assert (artifacts / name).is_file(), f"{name} must survive the clear"
    assert not (artifacts / "channel_1ghz.s1p").exists(), "this stem's stale export goes"
    assert (artifacts / "channel_1ghz.s4p").is_file()


def test_explicit_name_is_never_deleted_from_a_reused_directory(tmp_path: Path):
    """A user-named export is theirs: the clear never runs on its behalf."""

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    kept = artifacts / "custom.s4p"
    kept.write_text("previous round", encoding="utf-8")

    class _WritesNothing(_FakeLayout):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            self.exported_to = Path(output_file)
            return str(output_file)

    layout = _WritesNothing(ports=["Port1", "Port2", "Port3", "Port4"], file_port_count=4)
    result = _run_node(tmp_path, layout, {"touchstone_name": "custom.s4p"})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert kept.read_text(encoding="utf-8") == "previous round"
    assert payload["touchstone_path"].endswith("custom.s4p")


def test_explicit_name_glob_fallback_ignores_a_preexisting_sibling(tmp_path: Path):
    """A user name AEDT re-extends must not resolve to a file that was already there."""

    artifacts = tmp_path / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "custom.s4p").write_text("previous round", encoding="utf-8")

    class _WritesNothing(_FakeLayout):
        def export_touchstone(self, *, setup: str, sweep: str, output_file: str) -> str:
            self.exported_to = Path(output_file)
            return str(output_file)

    layout = _WritesNothing(ports=["Port1", "Port2", "Port3", "Port4"], file_port_count=4)
    result = _run_node(tmp_path, layout, {"touchstone_name": "custom.touchstone"})
    assert result["status"] == "succeeded", result
    payload = result["output_payload"]
    assert payload["touchstone_exists"] is False
    assert payload["touchstone_kind"] == "unknown"
    assert payload["touchstone_points"] == 0
    assert any(
        "no file was found" in warning for warning in payload["touchstone_parse_warnings"]
    )


def test_get_touchstone_data_failure_falls_back_to_the_file(tmp_path: Path):
    layout = _FakeLayout(
        ports=["Port1", "Port2", "Port3", "Port4"],
        file_port_count=4,
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


def test_touchstone_nports_from_data_is_bounded():
    assert _touchstone_nports_from_data(None) == 0
    assert _touchstone_nports_from_data([]) == 0
    assert _touchstone_nports_from_data([object()]) == 0
    assert _touchstone_nports_from_data([_FakeTouchstoneData(1, nports=4)]) == 4
    assert _touchstone_nports_from_data(_FakeTouchstoneData(1, nports=2)) == 2
