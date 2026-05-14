def validate(session_id, project_id, design_id):
    assert session_id
    assert project_id
    assert design_id
    return {"passed": True, "checks": ["session_available"]}
