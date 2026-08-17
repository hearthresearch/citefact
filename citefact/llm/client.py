"""LiteLLM wrapper: single entry point for every LLM call.

Never import provider SDKs directly; LiteLLM is the only gateway.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass

# litellm fetches a remote model-price map from GitHub at import time by
# default, which stalls startup on slow networks and breaks the
# no-network-beyond-the-LLM-provider rule. Pin it to the bundled local map
# BEFORE the import; respect the user's own setting if already exported.
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

import litellm  # noqa: E402  (env var above must precede this import)

log = logging.getLogger(__name__)

DEFAULT_MODEL = "anthropic/claude-sonnet-5"

litellm.suppress_debug_info = True
# Current Claude models (Sonnet 5, Opus 4.7+) reject non-default
# temperature/top_p/top_k with an error. drop_params makes litellm strip
# parameters the target model does not support instead of failing the
# call; models that do support them keep receiving our values.
litellm.drop_params = True


@dataclass
class LlmResult:
    text: str
    model: str
    cost_usd: float
    finish_reason: str | None


def resolve_model(cli_model: str | None, cli_provider: str | None) -> str:
    """--model > --provider default > CITEFACT_MODEL env > default.

    A bare --provider picks that provider's default model and takes
    precedence over the CITEFACT_MODEL env var, since an explicit CLI flag
    is a more specific request than an ambient environment setting. An
    unknown provider with no --model raises rather than synthesizing a
    nonsense model id.
    """
    if cli_model:
        return f"{cli_provider}/{cli_model}" if cli_provider and "/" not in cli_model else cli_model
    if cli_provider:
        defaults = {"anthropic": DEFAULT_MODEL, "openai": "openai/gpt-5", "ollama": "ollama/llama3.1"}
        try:
            return defaults[cli_provider]
        except KeyError:
            raise ValueError(f"No default model known for provider {cli_provider!r}; pass --model") from None
    env = os.environ.get("CITEFACT_MODEL")
    if env:
        return env
    return DEFAULT_MODEL


def supports_prompt_caching(model: str) -> bool:
    return model.startswith("anthropic/")


def call_llm(
    messages: list[dict],
    *,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 8192,
) -> LlmResult:
    response = litellm.completion(
        model=model, messages=messages,
        temperature=temperature, max_tokens=max_tokens,
    )
    choice = response.choices[0]
    try:
        cost = float(litellm.completion_cost(response))
    except Exception:  # unknown model pricing (e.g. local ollama)
        cost = 0.0
    return LlmResult(
        text=choice.message.content or "",
        model=getattr(response, "model", model),
        cost_usd=cost,
        finish_reason=getattr(choice, "finish_reason", None),
    )
