import json
import subprocess
import sys

from aedt_agent.layout.recorded_workflow import analyze_recorded_workflow


def _recorded_fixture():
    return '''
# Script Recorded by Ansys Electronics Desktop Version 2026.1.0
oTool.ImportExtracta("/boards/case.brd", "/runs/case.aedb", "/runs/case.xml")
oEditor.CutOutSubDesign([
    "NAME:Params",
    "Name:=", "case_cutout",
    ["NAME:Nets",
        "net:=", ["case:GND", True],
        "net:=", ["case:SRDS_0_RX0_N", True],
        "net:=", ["case:SRDS_0_RX0_P", True]
    ]
])
oEditor.ChangeProperty(["NAME:AllTabs", ["NAME:BaseElementTab", ["NAME:PropServers", "U1"], ["NAME:ChangedProps", ["NAME:Model Info"]]]])
oEditor.CreatePortsOnComponentsByNet(["NAME:Components", "U1"], ["NAME:Nets", "SRDS_0_RX0_N", "SRDS_0_RX0_P"], "Port", "0", "0", "0")
oEditor.CreateEdgePort(["NAME:Contents", "edge:=", ["et:=", "pe", "prim:=", "line__42040", "edge:=", 13]])
oDesign.EditHfssExtents(["NAME:HfssExportInfo", "AirHorExt:=", ["Ext:=", "3mm", "Dim:=", True], "AirPosZExt:=", ["Ext:=", "3mm", "Dim:=", True], "AirNegZExt:=", ["Ext:=", "3mm", "Dim:=", True], "OpenRegionType:=", "Radiation", "UseRadBound:=", True, "OperFreq:=", "5GHz"])
oDesign.DesignOptions(["NAME:options", "CausalMaterials:=", True, "MeshingMethod:=", "PhiPlus", "UseAlternativeMeshMethodsAsFallBack:=", True, "PhiMesherDeltaZRatio:=", 100000], 0)
oModule.Add(["NAME:Setup1", "SliderType:=", "Balanced", "Frequency:=", "10GHz", "MeshSizeFactor:=", 1.5, "HfssMesh:=", True, ["NAME:AdvancedSettings", "OrderBasis:=", -1, "MeshingMethod:=", "Auto", "PhiMesherDeltaZRatio:=", 100000], ["NAME:CurveApproximation", "ArcAngle:=", "10deg", "MaxPoints:=", 12, "UnionPolys:=", True]])
oModule.AddSweep("Setup1", ["NAME:Sweep1", "Data:=", "LIN 0GHz 67GHz 0.05GHz", "FreqSweepType:=", "kInterpolating", "EnforcePassivity:=", True, "InterpUseFullBasis:=", True, "MaxSolutions:=", 2500])
oModule.SetDiffPairs(["NAME:DiffPairs", "Pair:=", ["Pos:=", "Port1:SRDS_0_RX0_N", "Neg:=", "Port1:SRDS_0_RX0_P", "Dif:=", "Diff1"]])
oModule.CreateReport("S Parameter Plot1", "Standard", "Rectangular Plot", "Setup1 : Sweep1", [], [], ["Y Component:=", ["dB(S(Diff1,Diff1))"]])
oModule.CreateReport("TDR Impedance Plot1", "Standard", "Rectangular Plot", "Setup1 : Sweep1", [], [], ["Y Component:=", ["TDRZ(Diff1)"]])
oDesign.ChangeProperty(["NAME:AllTabs", ["NAME:LocalVariableTab", ["NAME:NewProps", ["NAME:r_cut_L3", "Value:=", "15mil"]]]])
oEditor.CreateCircleVoid(["NAME:Contents", "circle voidGeometry:=", ["Name:=", "circle void_48787", "LayerName:=", "ART03", "r:=", "13.95mil"]])
oEditor.CreateRectangleVoid(["NAME:Contents", "rect voidGeometry:=", ["Name:=", "rect void_48792", "LayerName:=", "ART03"]])
oProject.SaveAs("/runs/case.aedt", True)
oDesign.Analyze("Setup1")
'''


def test_analyze_recorded_workflow_extracts_operations_and_parameters(tmp_path):
    path = tmp_path / "recorded.py"
    path.write_text(_recorded_fixture(), encoding="utf-8")

    result = analyze_recorded_workflow(path)

    assert result["aedt_version"] == "2026.1.0"
    assert result["paths"]["brd"] == "/boards/case.brd"
    assert result["paths"]["aedb"] == "/runs/case.aedb"
    assert result["paths"]["aedt_project"] == "/runs/case.aedt"
    assert result["nets"]["signal"] == ["SRDS_0_RX0_N", "SRDS_0_RX0_P"]
    assert result["nets"]["reference"] == ["GND"]
    assert result["component"] == "U1"
    assert result["setup"]["name"] == "Setup1"
    assert result["design_options"]["MeshingMethod"] == "PhiPlus"
    assert result["design_options"]["PhiMesherDeltaZRatio"] == 100000
    assert result["hfss_extents"]["OpenRegionType"] == "Radiation"
    assert result["hfss_extents"]["AirHorExt"] == {"Ext": "3mm", "Dim": True}
    assert result["hfss_extents"]["AirPosZExt"] == {"Ext": "3mm", "Dim": True}
    assert result["hfss_extents"]["AirNegZExt"] == {"Ext": "3mm", "Dim": True}
    assert result["setup"]["options"]["SliderType"] == "Balanced"
    assert result["setup"]["options"]["MeshSizeFactor"] == 1.5
    assert result["setup"]["advanced_settings"]["OrderBasis"] == -1
    assert result["setup"]["advanced_settings"]["PhiMesherDeltaZRatio"] == 100000
    assert result["setup"]["curve_approximation"]["ArcAngle"] == "10deg"
    assert result["setup"]["curve_approximation"]["MaxPoints"] == 12
    assert result["setup"]["curve_approximation"]["UnionPolys"] is True
    assert result["sweep"]["stop_ghz"] == 67.0
    assert result["sweep"]["options"]["EnforcePassivity"] is True
    assert result["sweep"]["options"]["MaxSolutions"] == 2500
    assert result["optimization_variables"] == [{"name": "r_cut_L3", "value": "15mil"}]
    assert {"layer": "ART03", "kind": "circle"} in result["voids"]
    assert {"layer": "ART03", "kind": "rectangle"} in result["voids"]
    assert "create_layout_component_ports" in result["steps"]
    assert result["pyaedt_migration"]["CreatePortsOnComponentsByNet"]["preferred"] == "Hfss3dLayout.create_ports_on_component_by_nets"
    assert result["pyaedt_migration"]["CreateCircleVoid"]["fallback"].startswith("raw")


def test_render_recorded_workflow_html_contains_pyaedt_mapping(tmp_path):
    from aedt_agent.reporting.recorded_workflow_report import render_recorded_workflow_html

    path = tmp_path / "recorded.py"
    path.write_text(_recorded_fixture(), encoding="utf-8")
    analysis = analyze_recorded_workflow(path)

    html = render_recorded_workflow_html(analysis)

    assert "Stage C.5 录制工作流分析" in html
    assert "PyAEDT 优先迁移表" in html
    assert "Hfss3dLayout.create_ports_on_component_by_nets" in html
    assert "raw CreateCircleVoid" in html
    assert "r_cut_L3" in html
    assert "MeshingMethod" in html
    assert "PhiPlus" in html
    assert "InterpUseFullBasis" in html
    assert "ArcAngle" in html
    assert "MaxPoints" in html


def test_analyze_stage_c_recorded_workflow_cli_writes_json_and_html(tmp_path):
    path = tmp_path / "recorded.py"
    path.write_text(_recorded_fixture(), encoding="utf-8")
    output_json = tmp_path / "analysis.json"
    output_html = tmp_path / "analysis.html"

    result = subprocess.run(
        [
            sys.executable,
            "scripts/analyze_stage_c_recorded_workflow.py",
            "--source",
            str(path),
            "--output-json",
            str(output_json),
            "--output-html",
            str(output_html),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0
    assert output_json.exists()
    assert output_html.exists()
    assert json.loads(output_json.read_text(encoding="utf-8"))["component"] == "U1"
    assert "Stage C.5 recorded workflow analysis" in result.stdout
