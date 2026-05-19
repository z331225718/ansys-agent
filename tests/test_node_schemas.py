from aedt_agent.mcp.node_schemas import describe_node_schema, validate_node_inputs


def test_validate_node_inputs_applies_defaults():
    result = validate_node_inputs(
        "create_substrate",
        {"origin": [0, 0, 0], "size": [1, 2, 3], "material": "FR4_epoxy"},
    )

    assert result.passed is True
    assert result.inputs["name"] == "Substrate"
    assert result.inputs["units"] == "mm"


def test_validate_node_inputs_reports_missing_and_unknown_fields():
    result = validate_node_inputs("create_setup", {"extra": True})

    assert result.passed is False
    assert "missing required input: frequency" in result.errors
    assert "unknown input: extra" in result.errors


def test_validate_node_inputs_reports_wrong_type():
    result = validate_node_inputs("create_port", {"port_type": "lumped", "assignment": {"face": 1}})

    assert result.passed is False
    assert "wrong value for assignment: expected node output reference" in result.errors


def test_describe_node_schema_lists_required_inputs():
    description = describe_node_schema("create_substrate")

    assert description["required"] == ["material", "origin", "size"]
    assert "name" in description["optional"]


def test_sweep_schema_accepts_interpolating_type_alias():
    result = validate_node_inputs("create_sweep_or_export", {"setup": "Setup1", "type": "Interpolating"})

    assert result.passed is True
    assert result.inputs["sweep_type"] == "Interpolating"


def test_sweep_schema_rejects_unknown_sweep_type():
    result = validate_node_inputs("create_sweep_or_export", {"setup": "Setup1", "sweep_type": "adaptive"})

    assert result.passed is False
    assert "unsupported sweep type: adaptive" in result.errors
