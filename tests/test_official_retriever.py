
from aedt_agent.benchmark.models import BenchmarkTask
from aedt_agent.benchmark.official_retriever import GitNexusOfficialRetriever


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        task_id="L1_create_wave_port",
        level="L1",
        domain="hfss",
        requirement="Create a wave port on the end face of a microstrip line.",
        expected_workflow=["select_face", "create_port"],
        required_api_categories=["excitation"],
        validation_script="benchmarks/validation_scripts/validate_L1_create_wave_port.py",
    )


def test_gitnexus_retriever_queries_graph_and_examples(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    (examples / "hfss_wave_port.py").write_text(
        "from ansys.aedt.core import Hfss\napp = Hfss()\napp.wave_port(assignment=1, name='P1')\n",
        encoding="utf-8",
    )
    calls = []

    def fake_runner(cmd, timeout, capture_output, text, cwd=None):
        calls.append(cmd)

        class Result:
            returncode = 0
            stdout = '{"definitions":[{"name":"wave_port","filePath":"src/ansys/aedt/core/hfss.py","startLine":6571,"endLine":6788}],"processes":[]}'
            stderr = ""

        return Result()

    retriever = GitNexusOfficialRetriever(
        pyaedt_repo=tmp_path / "pyaedt",
        examples_repo=examples,
        subprocess_runner=fake_runner,
        top_k=3,
    )

    bundle = retriever.retrieve_bundle(_task())

    assert calls
    assert any("wave_port" in " ".join(call) for call in calls)
    assert bundle.evidence
    assert any(item.source_type == "gitnexus" for item in bundle.evidence)
    assert any(item.source_type == "example" for item in bundle.evidence)
    assert "hfss_wave_port.py" in bundle.to_prompt_context()


def test_gitnexus_retriever_uses_previous_error_log_in_query(tmp_path):
    captured = []

    def fake_runner(cmd, timeout, capture_output, text, cwd=None):
        captured.append(" ".join(cmd))

        class Result:
            returncode = 0
            stdout = "{}"
            stderr = ""

        return Result()

    retriever = GitNexusOfficialRetriever(
        pyaedt_repo=tmp_path / "pyaedt",
        subprocess_runner=fake_runner,
    )

    retriever.retrieve_bundle(_task(), previous_log="TypeError: wave_port got unexpected keyword start_object")

    assert any("unexpected keyword" in query for query in captured)


def test_gitnexus_retriever_prefers_http_eval_server(tmp_path):
    examples = tmp_path / "examples"
    examples.mkdir()
    calls = []

    def fake_http_post(url, payload, timeout):
        calls.append((url, payload, timeout))
        if url.endswith("/tool/query"):
            return "Standalone definitions:\n  Symbol wave_port -> src/ansys/aedt/core/hfss.py"
        if url.endswith("/tool/context"):
            return "Method wave_port -> src/ansys/aedt/core/hfss.py:6571-6788\nSource:\ndef wave_port(...): pass"
        return ""

    def failing_runner(*args, **kwargs):
        raise AssertionError("CLI should not be used when HTTP eval-server is available")

    retriever = GitNexusOfficialRetriever(
        pyaedt_repo=tmp_path / "pyaedt",
        examples_repo=examples,
        backend="gitnexus_http",
        gitnexus_url="http://127.0.0.1:4848",
        http_post=fake_http_post,
        subprocess_runner=failing_runner,
    )

    bundle = retriever.retrieve_bundle(_task())

    assert calls
    assert any(call[0].endswith("/tool/query") for call in calls)
    assert any(item.source_type == "gitnexus_http" for item in bundle.evidence)
