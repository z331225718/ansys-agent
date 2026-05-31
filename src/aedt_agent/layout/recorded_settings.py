from __future__ import annotations


def merge_recorded_layout_settings(params: dict[str, object], recorded_analysis: dict[str, object]) -> None:
    setup = recorded_analysis.get("setup") if isinstance(recorded_analysis.get("setup"), dict) else {}
    sweep = recorded_analysis.get("sweep") if isinstance(recorded_analysis.get("sweep"), dict) else {}
    sweep_options = sweep.get("options") if isinstance(sweep.get("options"), dict) else {}
    params["recorded_hfss_extents"] = dict(recorded_analysis.get("hfss_extents") or {})
    params["recorded_design_options"] = dict(recorded_analysis.get("design_options") or {})
    params["recorded_setup_options"] = dict(setup.get("options") or {})
    params["recorded_setup_advanced_settings"] = dict(setup.get("advanced_settings") or {})
    params["recorded_setup_curve_approximation"] = dict(setup.get("curve_approximation") or {})
    params["recorded_sweep_options"] = dict(sweep_options)
    if "UseQ3DForDC" in sweep_options:
        params["use_q3d_for_dc"] = sweep_options["UseQ3DForDC"]
    if "MaxSolutions" in sweep_options:
        params["interpolation_max_solutions"] = sweep_options["MaxSolutions"]
