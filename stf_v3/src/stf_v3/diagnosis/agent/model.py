"""The single model source (design doc D7 / blueprint §7.2).

``build_model`` turns settings into a Pydantic AI model object for the
OpenAI-compatible endpoint (Ollama today, vLLM after PROD-09).  A
non-local address is refused unless ``llm_allow_cloud`` is on (FM-19);
the cloud comparison model is built only when enabled.

qwen quirks handled here (PROD-09 will extend the profile): thinking
parts are never sent back to the model (Ollama returns them in a
separate ``reasoning`` field; re-sending them is wasted context at best,
FM-43), tool definitions are not marked ``strict``.

Author: Xiangzhu Yan
"""

from __future__ import annotations

import os
from typing import Any, Optional

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

from pydantic_ai.models import Model  # noqa: E402
from pydantic_ai.profiles import ModelProfile, merge_profile  # noqa: E402
from pydantic_ai.profiles.qwen import qwen_model_profile  # noqa: E402
from pydantic_ai.settings import ModelSettings  # noqa: E402

from stf_v3.settings import Settings, is_local_url  # noqa: E402


class ModelConfigError(RuntimeError):
    """The LLM settings are not acceptable (e.g. cloud address without opt-in)."""


def _profile_for(model_name: str) -> ModelProfile:
    base = qwen_model_profile(model_name) if "qwen" in model_name.lower() else None
    extra: ModelProfile = {
        "openai_chat_send_back_thinking_parts": False,
        "openai_supports_strict_tool_definition": False,
    }
    return merge_profile(base, extra) if base else extra


def build_model(settings: Settings, *, cloud: bool = False) -> Model:
    """Model object for the configured endpoint.

    Args:
        settings: V3 settings.
        cloud: Build the cloud comparison model instead of the local one.

    Raises:
        ModelConfigError: Cloud model requested but not enabled, or the
            local endpoint is not a local address and cloud is not allowed.
    """
    from pydantic_ai.models.openai import OpenAIChatModel
    from pydantic_ai.providers.openai import OpenAIProvider

    if cloud:
        if not settings.cloud_llm_enabled:
            raise ModelConfigError("cloud model requested but STF_V3_CLOUD_LLM_ENABLED is off")
        if not settings.cloud_llm_api_key:
            raise ModelConfigError("STF_V3_CLOUD_LLM_API_KEY is empty")
        base_url, model_name, api_key = (
            settings.cloud_llm_base_url, settings.cloud_llm_model, settings.cloud_llm_api_key,
        )
    else:
        base_url, model_name, api_key = settings.llm_base_url, settings.llm_model, settings.llm_api_key
        if not is_local_url(base_url) and not settings.llm_allow_cloud:
            raise ModelConfigError(
                f"STF_V3_LLM_BASE_URL {base_url!r} is not a local address; set "
                f"STF_V3_LLM_ALLOW_CLOUD=true to use a remote model (the VIN will be pseudonymised)"
            )
    provider = OpenAIProvider(base_url=base_url, api_key=api_key or "none")
    return OpenAIChatModel(model_name, provider=provider, profile=_profile_for(model_name))


def model_settings(settings: Settings, *, subagent: bool = False) -> ModelSettings:
    """Per-request settings (max tokens, temperature, read timeout)."""
    return ModelSettings(
        max_tokens=settings.subagent_max_tokens if subagent else settings.llm_max_tokens,
        temperature=settings.subagent_temperature if subagent else settings.llm_temperature,
        timeout=settings.llm_request_timeout_s,
    )


def model_is_local(settings: Settings, *, cloud: bool = False) -> bool:
    """Whether prompts may carry the raw VIN (local endpoint only)."""
    if cloud:
        return False
    return is_local_url(settings.llm_base_url)


def describe(model: Optional[Any]) -> str:
    """``"<system>:<model_name>"`` for reports and logs."""
    if model is None:
        return "unknown"
    name = getattr(model, "model_name", None) or getattr(model, "_model_name", "unknown")
    system = getattr(model, "system", None) or getattr(model, "_system", "")
    return f"{system}:{name}" if system else str(name)
