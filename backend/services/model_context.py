# -*- coding: utf-8 -*-
"""Model context-window resolution helpers."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional


OPENAI_MODEL_CONTEXT_WINDOWS: dict[str, int] = {
    "gpt-5": 400_000,
    "gpt-5-mini": 400_000,
    "gpt-5-nano": 400_000,
    "gpt-5.1": 400_000,
    "gpt-5.1-mini": 400_000,
    "gpt-5.1-nano": 400_000,
    "gpt-5.2": 400_000,
    "gpt-5.2-chat": 400_000,
    "gpt-5.5": 1_050_000,
    "gpt-4.1": 1_047_576,
    "gpt-4.1-mini": 1_047_576,
    "gpt-4.1-nano": 1_047_576,
    "gpt-4o": 128_000,
    "gpt-4o-mini": 128_000,
    "gpt-4": 8_192,
}

OPENAI_MODEL_OUTPUT_RESERVES: dict[str, int] = {
    "gpt-5": 128_000,
    "gpt-5-mini": 128_000,
    "gpt-5-nano": 128_000,
    "gpt-5.1": 128_000,
    "gpt-5.1-mini": 128_000,
    "gpt-5.1-nano": 128_000,
    "gpt-5.2": 128_000,
    "gpt-5.2-chat": 128_000,
    "gpt-5.5": 128_000,
    "gpt-4.1": 32_768,
    "gpt-4.1-mini": 32_768,
    "gpt-4.1-nano": 32_768,
    "gpt-4o": 16_384,
    "gpt-4o-mini": 16_384,
    "gpt-4": 4_096,
}

OPENAI_MODEL_INPUT_WINDOWS: dict[str, int] = {
    model_id: max(window - OPENAI_MODEL_OUTPUT_RESERVES.get(model_id, 0), 1)
    for model_id, window in OPENAI_MODEL_CONTEXT_WINDOWS.items()
}


@dataclass(frozen=True)
class ModelContextMetadata:
    model_id: str
    context_window: int
    input_window: Optional[int] = None
    output_reserve: Optional[int] = None
    source: str = "configured"


def normalize_model_id(model_id: str | None) -> str:
    return str(model_id or "").strip().lower()


def context_window_from_provider(provider_data: Any, model_id: str) -> Optional[int]:
    metadata = model_context_from_provider(provider_data, model_id)
    return metadata.context_window if metadata else None


def model_context_from_provider(provider_data: Any, model_id: str) -> Optional[ModelContextMetadata]:
    normalized_model_id = normalize_model_id(model_id)
    if not normalized_model_id or not isinstance(provider_data, Mapping):
        return None
    models = provider_data.get("models", [])
    if not isinstance(models, list):
        return None
    for model in models:
        if not isinstance(model, Mapping) or normalize_model_id(model.get("id")) != normalized_model_id:
            continue
        context_window = _positive_int(model.get("contextWindow"))
        if context_window is None:
            return None
        return ModelContextMetadata(
            model_id=str(model.get("id") or model_id),
            context_window=context_window,
            input_window=_positive_int(model.get("inputWindow")),
            output_reserve=_positive_int(model.get("maxTokens")) or _positive_int(model.get("outputReserve")),
            source="configured",
        )
    return None


def openai_model_context_metadata(model_id: str) -> Optional[ModelContextMetadata]:
    normalized_model_id = normalize_model_id(model_id)
    context_window = OPENAI_MODEL_CONTEXT_WINDOWS.get(normalized_model_id)
    if context_window is None:
        return None
    return ModelContextMetadata(
        model_id=model_id,
        context_window=context_window,
        input_window=OPENAI_MODEL_INPUT_WINDOWS.get(normalized_model_id),
        output_reserve=OPENAI_MODEL_OUTPUT_RESERVES.get(normalized_model_id),
        source="openai_reference",
    )


def resolve_model_context_metadata(
    *,
    model_id: str,
    registry_model_info: Optional[Mapping[str, Any]] = None,
    agent_provider: Any = None,
    global_provider: Any = None,
    peer_providers: Optional[list[Any]] = None,
) -> Optional[ModelContextMetadata]:
    normalized_model_id = normalize_model_id(model_id)
    if not normalized_model_id:
        return None

    if isinstance(registry_model_info, Mapping):
        context_window = _positive_int(registry_model_info.get("context_window"))
        if context_window is not None:
            return ModelContextMetadata(
                model_id=model_id,
                context_window=context_window,
                input_window=_positive_int(registry_model_info.get("input_window")),
                output_reserve=_positive_int(registry_model_info.get("max_tokens")) or _positive_int(registry_model_info.get("output_reserve")),
                source="registry",
            )

    for provider in [agent_provider, global_provider, *(peer_providers or [])]:
        metadata = model_context_from_provider(provider, model_id)
        if metadata is not None:
            return metadata

    return openai_model_context_metadata(model_id)


def _positive_int(value: Any) -> Optional[int]:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None
