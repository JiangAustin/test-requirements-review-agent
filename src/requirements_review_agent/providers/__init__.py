from __future__ import annotations

from collections.abc import Mapping

from httpx import AsyncBaseTransport

from ..models import ProviderMode
from .base import AnalysisProvider, _validate_config
from .openai import OpenAICompatibleProvider, SleepCallable


def build_provider(
    mode: ProviderMode,
    env: Mapping[str, str],
    *,
    transport: AsyncBaseTransport | None = None,
    sleep: SleepCallable | None = None,
) -> AnalysisProvider | None:
    if mode is ProviderMode.COPILOT:
        return None

    base_url, model, api_key = _validate_config(mode, env)
    if transport is not None and sleep is not None:
        return OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            transport=transport,
            sleep=sleep,
        )
    if transport is not None:
        return OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            transport=transport,
        )
    if sleep is not None:
        return OpenAICompatibleProvider(
            base_url=base_url,
            model=model,
            api_key=api_key,
            sleep=sleep,
        )
    return OpenAICompatibleProvider(
        base_url=base_url,
        model=model,
        api_key=api_key,
    )


__all__ = ["AnalysisProvider", "OpenAICompatibleProvider", "build_provider"]