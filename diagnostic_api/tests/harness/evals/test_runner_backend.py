"""Unit tests for eval LLM backend selection (model bake-offs).

``EVAL_LLM_BACKEND`` switches the manual-agent eval between the
Ollama-native client (default; ``think=False`` suppression) and a
plain OpenAI-compatible client (the vLLM path, where thinking
suppression is server-side).  ``EVAL_LLM_MODEL`` /
``EVAL_LLM_ENDPOINT`` parameterise candidate models without
touching code.

Imports the runner lazily inside tests: it pulls in the app/agent
stack, which must not break collection in offline environments
(tiktoken download gotcha).

Author: Li-Ta Hsu
"""

from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _clean_runner_caches(monkeypatch):  # noqa: ANN001, ANN202
    """Reset the runner's process caches around every test."""
    from tests.harness.evals import runner

    runner._reset_cache_for_testing()
    yield
    runner._reset_cache_for_testing()


def test_default_backend_is_ollama_native(monkeypatch) -> None:  # noqa: ANN001
    """Unset env builds the Ollama-native client (think off)."""
    from app.harness.deps import OllamaNativeLLMClient
    from tests.harness.evals import runner

    monkeypatch.delenv("EVAL_LLM_BACKEND", raising=False)
    deps = runner._build_deps_for_endpoint("http://127.0.0.1:11434")
    assert isinstance(deps.llm_client, OllamaNativeLLMClient)


def test_openai_backend_builds_openai_client(monkeypatch) -> None:  # noqa: ANN001
    """EVAL_LLM_BACKEND=openai builds the OpenAI-compat client."""
    from app.harness.deps import OpenAILLMClient
    from tests.harness.evals import runner

    monkeypatch.setenv("EVAL_LLM_BACKEND", "openai")
    deps = runner._build_deps_for_endpoint("http://127.0.0.1:8010")
    assert isinstance(deps.llm_client, OpenAILLMClient)


def test_unknown_backend_raises(monkeypatch) -> None:  # noqa: ANN001
    """Typo'd backend fails fast instead of silently defaulting."""
    from tests.harness.evals import runner

    monkeypatch.setenv("EVAL_LLM_BACKEND", "vllm")
    with pytest.raises(ValueError, match="EVAL_LLM_BACKEND"):
        runner._build_deps_for_endpoint("http://127.0.0.1:8010")


def test_model_override_applies(monkeypatch) -> None:  # noqa: ANN001
    """EVAL_LLM_MODEL overrides the config's model identifier."""
    from tests.harness.evals import runner

    monkeypatch.setenv("EVAL_LLM_BACKEND", "openai")
    monkeypatch.setenv("EVAL_LLM_MODEL", "Qwen/Qwen3.6-27B-FP8")
    deps = runner._build_deps_for_endpoint("http://127.0.0.1:8010")
    assert deps.config.model == "Qwen/Qwen3.6-27B-FP8"


def test_endpoint_override_reaches_default_deps(monkeypatch) -> None:  # noqa: ANN001
    """EVAL_LLM_ENDPOINT redirects the default deps target."""
    from app.harness.deps import OllamaNativeLLMClient
    from tests.harness.evals import runner

    monkeypatch.delenv("EVAL_LLM_BACKEND", raising=False)
    monkeypatch.setenv(
        "EVAL_LLM_ENDPOINT", "http://127.0.0.1:9999",
    )
    deps = runner._build_default_deps()
    assert isinstance(deps.llm_client, OllamaNativeLLMClient)
    assert "9999" in deps.llm_client._chat_url