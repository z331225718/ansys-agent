from aedt_agent.layout.recorded_settings import merge_recorded_layout_settings


def test_merge_recorded_layout_settings_copies_setup_sweep_extents_and_design_options():
    params = {}
    recorded_analysis = {
        "hfss_extents": {"AirHorExt": "3mm"},
        "design_options": {"DesignMode": "Hfss"},
        "setup": {
            "options": {"AdaptiveSettings": {"MaxPasses": 8}},
            "advanced_settings": {"PhiPlusMesher": True},
            "curve_approximation": {"ArcAngle": "30deg", "MaxArcPoints": 8},
        },
        "sweep": {"options": {"UseQ3DForDC": True, "MaxSolutions": 250}},
    }

    merge_recorded_layout_settings(params, recorded_analysis)

    assert params["recorded_hfss_extents"] == {"AirHorExt": "3mm"}
    assert params["recorded_design_options"] == {"DesignMode": "Hfss"}
    assert params["recorded_setup_options"] == {"AdaptiveSettings": {"MaxPasses": 8}}
    assert params["recorded_setup_advanced_settings"] == {"PhiPlusMesher": True}
    assert params["recorded_setup_curve_approximation"] == {"ArcAngle": "30deg", "MaxArcPoints": 8}
    assert params["recorded_sweep_options"] == {"UseQ3DForDC": True, "MaxSolutions": 250}
    assert params["use_q3d_for_dc"] is True
    assert params["interpolation_max_solutions"] == 250
