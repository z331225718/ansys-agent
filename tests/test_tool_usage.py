from aedt_agent.benchmark.tool_usage import analyze_tool_usage


def test_tool_usage_detects_gitnexus_before_code():
    transcript = """
{"type":"assistant","message":{"content":[{"type":"tool_use","name":"mcp__gitnexus__query","input":{"query":"wave_port"}}]}}
{"type":"assistant","message":{"content":[{"type":"text","text":"app.wave_port(assignment='BoxWG1')"}]}}
"""

    usage = analyze_tool_usage(transcript, "app.wave_port(assignment='BoxWG1')")

    assert usage["used_tools"] is True
    assert usage["gitnexus_query_count"] >= 1
    assert "mcp__gitnexus__query" in usage["tool_call_names"]
    assert usage["retrieval_before_code"] is True


def test_tool_usage_handles_plain_code_without_tools():
    usage = analyze_tool_usage("app.modeler.create_box([0,0,0], [1,1,1])")

    assert usage["used_tools"] is False
    assert usage["retrieval_before_code"] is False
