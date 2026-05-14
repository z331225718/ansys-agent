def validate(
    session_id,
    project_id,
    design_id,
    model_info=None,
    expected_outputs=None,
    node_steps=None,
    known_failure_modes=None,
):
    assert session_id
    assert project_id
    assert design_id
    checks = ["session_available"]
    failures = []
    model_info = model_info or {}
    for expected in expected_outputs or []:
        if _has_expected_output(str(expected), model_info):
            checks.append(f"{expected}_present")
        else:
            failures.append(f"missing expected output: {expected}")
    if "wrong_face_selected_for_port" in (known_failure_modes or []):
        port_check = _validate_port_uses_selected_face(node_steps or [])
        if port_check:
            checks.append(port_check)
        else:
            failures.append("wave port assignment is not traceable to a selected face")
    return {
        "passed": not failures,
        "checks": checks,
        "failures": failures,
        "failure_type": "validation_fail" if failures else "",
        "log": "; ".join(failures),
    }


def _has_expected_output(expected, model_info):
    objects = model_info.get("objects", {})
    ports = model_info.get("ports", {})
    boundaries = model_info.get("boundaries", {})
    setups = model_info.get("setups", {})
    sweeps = model_info.get("sweeps", {})
    normalized = expected.lower()
    object_names = {str(name).lower(): data for name, data in objects.items()}

    if normalized == "substrate":
        return _object_name_or_material_contains(object_names, ("substrate", "fr4", "dielectric"))
    if normalized == "material_assignment":
        return any(str(data.get("material", "")).strip() for data in objects.values() if isinstance(data, dict))
    if normalized == "conductor":
        return _object_name_or_material_contains(object_names, ("conductor", "trace", "strip", "patch", "copper", "pec"))
    if normalized == "air_region":
        return _object_name_or_material_contains(object_names, ("air", "region"))
    if normalized == "radiation_boundary":
        return _mapping_value_contains(boundaries, ("radiation", "open"))
    if normalized == "wave_port":
        return _mapping_value_contains(ports, ("wave", "port"))
    if normalized == "lumped_port":
        return _mapping_value_contains(ports, ("lumped", "port"))
    if normalized == "setup":
        return bool(setups)
    if normalized == "sweep":
        return bool(sweeps)
    if normalized == "microstrip_line":
        return (
            _has_expected_output("substrate", model_info)
            and _has_expected_output("conductor", model_info)
            and _has_expected_output("radiation_boundary", model_info)
        )
    if normalized == "dipole_antenna":
        return _has_expected_output("conductor", model_info) and _has_expected_output("air_region", model_info)
    if normalized == "patch_probe_feed":
        return _has_expected_output("substrate", model_info) and _has_expected_output("conductor", model_info)
    if normalized == "simple_filter":
        return _has_expected_output("conductor", model_info) and bool(ports)
    if normalized == "ground":
        return _object_name_or_material_contains(object_names, ("ground", "gnd"))
    return normalized in object_names or normalized in {str(name).lower() for name in ports}


def _object_name_or_material_contains(objects, needles):
    for name, data in objects.items():
        material = str(data.get("material", "")).lower() if isinstance(data, dict) else ""
        if any(needle in name or needle in material for needle in needles):
            return True
    return False


def _mapping_value_contains(mapping, needles):
    for name, data in mapping.items():
        text = f"{name} {data}".lower()
        if any(needle in text for needle in needles):
            return True
    return False


def _validate_port_uses_selected_face(node_steps):
    selected_face_ids = set()
    for step in node_steps:
        if step.get("node_id") != "select_face":
            continue
        output = step.get("output", {})
        selected_face_id = output.get("selected_face_id")
        if selected_face_id is not None:
            selected_face_ids.add(selected_face_id)
    if not selected_face_ids:
        return ""
    for step in node_steps:
        if step.get("node_id") != "create_port":
            continue
        inputs = step.get("inputs", {})
        if str(inputs.get("port_type", "")).lower() not in {"wave", "wave_port"}:
            continue
        if inputs.get("assignment") in selected_face_ids:
            return "wave_port_uses_selected_face"
    return ""
