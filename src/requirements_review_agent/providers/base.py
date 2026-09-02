from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol

from ..analysis import AnalysisBatch
from ..errors import PROVIDER_UNAVAILABLE, ReviewError, ReviewException
from ..models import AnalysisSubmission, ProviderMode


class AnalysisProvider(Protocol):
    async def analyze(self, batch: AnalysisBatch) -> AnalysisSubmission: ...


def provider_unavailable(
    message: str,
    *,
    status: int | None = None,
    attempt: int | None = None,
    failure_type: str,
) -> ReviewException:
    details: dict[str, object] = {"type": failure_type}
    if status is not None:
        details["status"] = status
    if attempt is not None:
        details["attempt"] = attempt
    return ReviewException(
        ReviewError(
            code=PROVIDER_UNAVAILABLE,
            message=message,
            details=details,
        )
    )


def _required(env: Mapping[str, str], name: str, *, require_non_empty: bool = True) -> str | None:
    value = env.get(name)
    if value is None:
        return None
    if require_non_empty and not value.strip():
        return None
    return value.strip()


def _validate_config(mode: ProviderMode, env: Mapping[str, str]) -> tuple[str, str, str | None]:
    if mode is ProviderMode.COMPANY_API:
        base_url = _required(env, "RRA_COMPANY_BASE_URL")
        model = _required(env, "RRA_COMPANY_MODEL")
        api_key = _required(env, "RRA_COMPANY_API_KEY")
        if base_url is None or model is None or api_key is None:
            raise provider_unavailable(
                "Provider configuration is unavailable.",
                failure_type="config",
            )
        return base_url, model, api_key

    if mode is ProviderMode.LOCAL:
        base_url = _required(env, "RRA_LOCAL_BASE_URL")
        model = _required(env, "RRA_LOCAL_MODEL")
        if base_url is None or model is None:
            raise provider_unavailable(
                "Provider configuration is unavailable.",
                failure_type="config",
            )
        return base_url, model, None

    raise provider_unavailable(
        "Provider mode is unsupported.",
        failure_type="config",
    )


def normalize_base_url(base_url: str) -> str:
    return base_url.rstrip("/")
