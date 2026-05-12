from aedt_agent.benchmark.semantic_lite import check_semantic_lite


def test_missing_step_detected():
    from aedt_agent.benchmark.models import BenchmarkTask

    task = BenchmarkTask.from_dict(
        {
            "task_id": "L3_test",
            "level": "L3",
            "domain": "hfss",
            "requirement": "Create patch antenna",
            "allowed_nodes": ["create_substrate", "create_airbox"],
            "expected_workflow": ["create_substrate", "create_airbox"],
            "required_api_categories": ["geometry", "boundary"],
            "reference_script": "",
            "validation_script": "",
            "expected_outputs": [],
            "known_failure_modes": [],
            "grading": {},
        }
    )

    code = "app.modeler.create_box([0,0,0],[1,1,1], name='substrate')"
    result = check_semantic_lite(code, task, api_semantics=[], traps=[])

    assert result.passed is False
    assert any("create_airbox" in v for v in result.violations)


def test_missing_dependency_detected():
    code = "app.wave_port(assignment='face1', name='Port1')"
    result = check_semantic_lite(code, task=None, api_semantics=[], traps=[])

    assert result.passed is False
    assert any("get_object_faces" in v or "select_face" in v for v in result.violations)


def test_valid_code_passes():
    code = """
app.modeler.create_box([0,0,0],["20mm","15mm","0.8mm"], name='substrate', material='FR4_epoxy')
app.assign_material('substrate', 'FR4_epoxy')
app.modeler.create_region()
app.assign_radiation_boundary_to_objects('region')
"""
    result = check_semantic_lite(code, task=None, api_semantics=[], traps=[])
    assert result.passed is True
