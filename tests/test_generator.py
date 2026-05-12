from aedt_agent.benchmark.generator import (
    CodeGenerator,
    DefaultCodeGenerator,
    FileGenerator,
    OpenAIGenerator,
    create_generator_from_env,
)
from aedt_agent.benchmark.prompt_templates import build_prompt


def test_build_prompt_group_a_contains_only_requirement():
    prompt = build_prompt(group="A", requirement="Create a 2.4GHz patch antenna", context="")
    assert "2.4GHz patch antenna" in prompt
    assert "API whitelist" not in prompt


def test_build_prompt_group_c_contains_full_context():
    prompt = build_prompt(
        group="C",
        requirement="Create a 2.4GHz patch antenna",
        context="API whitelist:\n- Hfss.modeler.create_box\n\nCommon traps:\n- missing_ground_plane",
    )
    assert "API whitelist" in prompt
    assert "missing_ground_plane" in prompt


def test_file_generator_reads_from_disk(tmp_path):
    code_path = tmp_path / "test_code.py"
    code_path.write_text("app.modeler.create_box([0,0,0],[1,1,1])", encoding="utf-8")
    gen: CodeGenerator = FileGenerator(base_dir=tmp_path)
    code = gen.generate(context="any", filename="test_code.py")
    assert "create_box" in code


def test_default_generator_raises_not_implemented():
    gen = DefaultCodeGenerator()
    try:
        gen.generate(context="test")
        assert False, "Should have raised NotImplementedError"
    except NotImplementedError:
        pass


def test_create_generator_from_env_defaults(monkeypatch):
    monkeypatch.delenv("AEDT_AGENT_GENERATOR", raising=False)
    assert isinstance(create_generator_from_env(), DefaultCodeGenerator)


def test_openai_generator_reads_chat_completions(monkeypatch):
    import io
    import json

    captured = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "choices": [
                        {"message": {"content": "app.modeler.create_box([0,0,0],[1,1,1])"}}
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout=30):
        captured["url"] = request.full_url
        captured["body"] = json.loads(request.data.decode("utf-8"))
        captured["auth"] = request.headers.get("Authorization")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    gen = OpenAIGenerator(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
    )

    code = gen.generate(context="Requirement:\nCreate a box")

    assert "create_box" in code
    assert captured["url"].endswith("/chat/completions")
    assert captured["body"]["model"] == "test-model"
    assert captured["auth"] == "Bearer test-key"


def test_create_generator_from_env_openai(monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_GENERATOR", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "k")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://example.test/v1")
    monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
    assert isinstance(create_generator_from_env(), OpenAIGenerator)


def test_openai_generator_retries_timeout(monkeypatch):
    import json

    calls = {"count": 0}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return json.dumps(
                {
                    "choices": [
                        {"message": {"content": "app.modeler.create_box([0,0,0],[1,1,1])"}}
                    ]
                }
            ).encode("utf-8")

    def fake_urlopen(request, timeout=30):
        calls["count"] += 1
        if calls["count"] == 1:
            raise TimeoutError("timed out")
        return FakeResponse()

    monkeypatch.setattr("urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("time.sleep", lambda _: None)
    gen = OpenAIGenerator(
        base_url="https://example.test/v1",
        api_key="test-key",
        model="test-model",
        max_retries=1,
        retry_delay=0.0,
    )

    code = gen.generate(context="Requirement:\nCreate a box")

    assert "create_box" in code
    assert calls["count"] == 2
