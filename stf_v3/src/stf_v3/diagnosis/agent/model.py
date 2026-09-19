"""The single model source + adapter profiles (design doc D7, PROD-09).

``build_model`` turns settings into a Pydantic AI model object for the
OpenAI-compatible endpoint: the server's vLLM by default, Ollama as the
fallback, and a cloud comparison endpoint only through ``cloud=True``.
``resolve_profile`` picks the adapter profile from URL + model name (or
the explicit ``llm_profile`` setting):

* ``qwen-vllm``   -- Qwen served by vLLM: thinking switched off per request
  (``chat_template_kwargs.enable_thinking=false``, on top of the server
  default), thinking parts never sent back, tool definitions not marked
  strict.
* ``qwen-ollama`` -- Qwen served by Ollama: thinking cannot be disabled on
  ``/v1`` (it arrives in a separate field and is discarded); PROD-08
  budgets.
* ``generic``     -- anything else, including every cloud model: no
  vendor-specific request fields at all (FM-29).

Per-profile budget defaults live here as well (FM-8): a setting left at
``None`` takes the profile default; an explicit setting always wins.
Never use Pydantic AI's unified ``thinking`` model setting on these
endpoints: for OpenAI-compatible chat it becomes ``reasoning_effort``,
which vLLM and Ollama do not implement (FM-29).

Author: Xiangzhu Yan
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any, Dict, Optional
from urllib.parse import urlsplit

os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")

import structlog  # noqa: E402
from pydantic_ai.models import Model  # noqa: E402
from pydantic_ai.models.openai import OpenAIChatModel  # noqa: E402
from pydantic_ai.profiles import ModelProfile, merge_profile  # noqa: E402
from pydantic_ai.profiles.qwen import qwen_model_profile  # noqa: E402
from pydantic_ai.settings import ModelSettings  # noqa: E402

from stf_v3.settings import Settings, is_local_url  # noqa: E402

logger = structlog.get_logger(__name__)

PROFILE_QWEN_VLLM = "qwen-vllm"
PROFILE_QWEN_OLLAMA = "qwen-ollama"
PROFILE_GENERIC = "generic"
PROFILES = (PROFILE_QWEN_VLLM, PROFILE_QWEN_OLLAMA, PROFILE_GENERIC)

_OLLAMA_PORT = 11434

# What the qwen-vLLM profile adds to every request (FM-29): the server
# already defaults to thinking off; this makes each request explicit.
VLLM_NO_THINKING_EXTRA_BODY: Dict[str, Any] = {
    "chat_template_kwargs": {"enable_thinking": False},
}

# Budget / request defaults per profile (FM-8).  The Ollama row is
# PROD-08's (36 s per call with thinking on); the vLLM row was set from
# the PROD-09 server runs (see the dev plan entry); cloud models get the
# generic row.  Every value can be overridden by its ``STF_V3_*`` setting.
_PROFILE_DEFAULTS: Dict[str, Dict[str, Any]] = {
    PROFILE_QWEN_VLLM: dict(
        wall_clock_s=900.0, request_limit=60, tool_calls_limit=100, total_tokens_limit=1_000_000,
        subagent_wall_clock_s=180.0, subagent_request_limit=12, subagent_max_tokens=12_288,
        subagent_temperature=0.2, llm_max_tokens=8192, llm_temperature=0.3, request_timeout_s=180.0,
    ),
    PROFILE_QWEN_OLLAMA: dict(
        wall_clock_s=1200.0, request_limit=80, tool_calls_limit=120, total_tokens_limit=600_000,
        subagent_wall_clock_s=240.0, subagent_request_limit=12, subagent_max_tokens=12_288,
        subagent_temperature=0.2, llm_max_tokens=8192, llm_temperature=0.3, request_timeout_s=300.0,
    ),
    PROFILE_GENERIC: dict(
        wall_clock_s=900.0, request_limit=80, tool_calls_limit=120, total_tokens_limit=600_000,
        subagent_wall_clock_s=240.0, subagent_request_limit=12, subagent_max_tokens=12_288,
        subagent_temperature=0.2, llm_max_tokens=8192, llm_temperature=0.3, request_timeout_s=180.0,
    ),
}


class ModelConfigError(RuntimeError):
    """The LLM settings are not acceptable (e.g. cloud address without opt-in)."""


@dataclass(frozen=True)
class ModelSource:
    """Where a model object points and which adapter profile it uses.

    Recorded in ``session_start`` and in the report so a run can always
    be told apart as local vs cloud and by profile (FM-30 / FM-31).
    """

    kind: str          # "local" | "cloud"
    base_url: str
    model_name: str
    profile: str

    @property
    def host(self) -> str:
        return urlsplit(self.base_url).netloc or self.base_url

    @property
    def is_local(self) -> bool:
        return self.kind == "local"

    def label(self) -> str:
        """``"local qwen-vllm @127.0.0.1:8010"`` -- never includes a key."""
        return f"{self.kind} {self.profile} @{self.host}"

    def to_dict(self) -> Dict[str, str]:
        return {"kind": self.kind, "host": self.host, "model": self.model_name, "profile": self.profile}


class StfChatModel(OpenAIChatModel):
    """``OpenAIChatModel`` that remembers its ``ModelSource``."""

    def __init__(self, source: ModelSource, *, provider: Any, profile: ModelProfile) -> None:
        super().__init__(source.model_name, provider=provider, profile=profile)
        self.stf_source = source


def profile_defaults(profile: str) -> Dict[str, Any]:
    """Copy of the budget / request defaults for ``profile``.

    Raises:
        ModelConfigError: Unknown profile name.
    """
    if profile not in _PROFILE_DEFAULTS:
        raise ModelConfigError(f"unknown model profile {profile!r} (choose from {', '.join(PROFILES)})")
    return dict(_PROFILE_DEFAULTS[profile])


def resolve_profile(base_url: str, model_name: str, explicit: str = "auto") -> str:
    """Adapter profile for an endpoint + model name (FM-7 / FM-30).

    ``explicit`` other than ``auto`` is validated and returned as is.
    Otherwise: a Qwen model on a local endpoint is ``qwen-ollama`` when the
    endpoint is Ollama's port or the name carries an Ollama tag
    (``qwen3.5:27b-q8_0``), else ``qwen-vllm``; everything else --
    including every non-local endpoint -- is ``generic``.  A local
    endpoint that lands on ``generic`` is logged as a warning because it
    means no thinking control at all.
    """
    if explicit and explicit != "auto":
        if explicit not in PROFILES:
            raise ModelConfigError(f"STF_V3_LLM_PROFILE {explicit!r} is not one of {', '.join(PROFILES)} or auto")
        return explicit
    local = is_local_url(base_url)
    name = (model_name or "").lower()
    if local and "qwen" in name:
        port = urlsplit(base_url).port
        ollama_tag = ":" in model_name and "/" not in model_name
        if port == _OLLAMA_PORT or ollama_tag:
            return PROFILE_QWEN_OLLAMA
        return PROFILE_QWEN_VLLM
    if local:
        logger.warning("model.profile_generic", base_url=base_url, model=model_name,
                       hint="no thinking control for this model; set STF_V3_LLM_PROFILE explicitly")
    return PROFILE_GENERIC


def select_profile(settings: Settings, *, cloud: bool = False) -> str:
    """Profile for the configured local model (or the cloud comparison one)."""
    if cloud:
        return resolve_profile(settings.cloud_llm_base_url, settings.cloud_llm_model, "auto")
    return resolve_profile(settings.llm_base_url, settings.llm_model, settings.llm_profile)


def _profile_for(profile: str, model_name: str) -> ModelProfile:
    base = qwen_model_profile(model_name) if profile.startswith("qwen") else None
    extra: ModelProfile = {
        "openai_chat_send_back_thinking_parts": False,
        "openai_supports_strict_tool_definition": False,
    }
    return merge_profile(base, extra) if base else extra


def build_model(settings: Settings, *, cloud: bool = False, http_client: Any = None) -> Model:
    """Model object for the configured endpoint.

    Args:
        settings: V3 settings.
        cloud: Build the cloud comparison model instead of the local one.
        http_client: Optional ``httpx.AsyncClient`` (tests inject a mock
            transport to inspect the request body).

    Raises:
        ModelConfigError: Cloud model requested but not enabled or without
            a key; or the local endpoint is not a local address and cloud
            is not allowed; or an unknown explicit profile.
    """
    from pydantic_ai.providers.openai import OpenAIProvider

    if cloud:
        if not settings.cloud_llm_enabled:
            raise ModelConfigError("cloud model requested but STF_V3_CLOUD_LLM_ENABLED is off")
        api_key = settings.cloud_api_key
        if not api_key:
            raise ModelConfigError("no cloud key: set STF_V3_CLOUD_LLM_API_KEY or OPENROUTER_API_KEY")
        base_url, model_name = settings.cloud_llm_base_url, settings.cloud_llm_model
        source = ModelSource("cloud", base_url, model_name, resolve_profile(base_url, model_name, "auto"))
    else:
        base_url, model_name, api_key = settings.llm_base_url, settings.llm_model, settings.llm_api_key
        local = is_local_url(base_url)
        if not local and not settings.llm_allow_cloud:
            raise ModelConfigError(
                f"STF_V3_LLM_BASE_URL {base_url!r} is not a local address; set "
                f"STF_V3_LLM_ALLOW_CLOUD=true to use a remote model (the VIN will be pseudonymised)"
            )
        profile = resolve_profile(base_url, model_name, settings.llm_profile)
        source = ModelSource("local" if local else "cloud", base_url, model_name, profile)
    provider = OpenAIProvider(base_url=base_url, api_key=api_key or "none", http_client=http_client)
    logger.info("model.source", **source.to_dict())
    return StfChatModel(source, provider=provider, profile=_profile_for(source.profile, model_name))


def source_of(model: Optional[Any]) -> Optional[ModelSource]:
    """The ``ModelSource`` a model was built with (None for test models)."""
    return getattr(model, "stf_source", None)


def source_label(model: Optional[Any]) -> str:
    """``ModelSource.label()`` or ``"test"`` for models without a source."""
    src = source_of(model)
    return src.label() if src else "test"


def model_settings(
    settings: Settings,
    *,
    subagent: bool = False,
    model: Optional[Any] = None,
    profile: Optional[str] = None,
) -> ModelSettings:
    """Per-request settings for a profile (max tokens, temperature, timeout,
    and the profile's extra request body).

    The profile comes from ``profile``, else from the model object's
    source (so sub-agents follow whatever model the run uses, including
    a cloud comparison model), else from the local settings.
    """
    src = source_of(model)
    name = profile or (src.profile if src else select_profile(settings))
    d = profile_defaults(name)
    max_tokens = (settings.subagent_max_tokens if subagent else settings.llm_max_tokens)
    temperature = (settings.subagent_temperature if subagent else settings.llm_temperature)
    ms = ModelSettings(
        max_tokens=d["subagent_max_tokens" if subagent else "llm_max_tokens"] if max_tokens is None else max_tokens,
        temperature=d["subagent_temperature" if subagent else "llm_temperature"] if temperature is None else temperature,
        timeout=d["request_timeout_s"] if settings.llm_request_timeout_s is None else settings.llm_request_timeout_s,
    )
    if name == PROFILE_QWEN_VLLM:
        ms["extra_body"] = {"chat_template_kwargs": dict(VLLM_NO_THINKING_EXTRA_BODY["chat_template_kwargs"])}
    return ms


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
