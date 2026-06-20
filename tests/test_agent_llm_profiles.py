from __future__ import annotations

from aedt_agent.agent.llm import LlmConfig


def test_llm_config_uses_profile_specific_environment(monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_LLM_MODEL", "global-model")
    monkeypatch.setenv("AEDT_AGENT_LLM_API_KEY", "global-key")
    monkeypatch.setenv("AEDT_AGENT_LLM_BASE_URL", "https://global.example/v1")
    monkeypatch.setenv("AEDT_AGENT_LLM_LOW_COST_MODEL", "cheap-model")
    monkeypatch.setenv("AEDT_AGENT_LLM_LOW_COST_API_KEY", "cheap-key")
    monkeypatch.setenv("AEDT_AGENT_LLM_LOW_COST_TEMPERATURE", "0.05")
    monkeypatch.setenv("AEDT_AGENT_LLM_LOW_COST_MAX_TOKENS", "512")

    config = LlmConfig.from_env(profile="low_cost")

    assert config.model == "cheap-model"
    assert config.api_key == "cheap-key"
    assert config.base_url == "https://global.example/v1"
    assert config.temperature == 0.05
    assert config.max_tokens == 512


def test_llm_config_supports_legacy_profile_prefix(monkeypatch):
    monkeypatch.setenv("AEDT_AGENT_LLM_MODEL", "global-model")
    monkeypatch.setenv("AEDT_AGENT_HIGH_REASONING_LLM_MODEL", "reasoning-model")

    config = LlmConfig.from_env(profile="high_reasoning")

    assert config.model == "reasoning-model"
