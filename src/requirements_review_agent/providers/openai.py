from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from pydantic import ValidationError

from ..analysis import AnalysisBatch
from ..errors import ReviewException
from ..models import AnalysisSubmission
from .base import normalize_base_url, provider_unavailable

SleepCallable = Callable[[float], Awaitable[None]]


async def _default_sleep(delay: float) -> None:
    import asyncio

    await asyncio.sleep(delay)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str | None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: SleepCallable = _default_sleep,
    ) -> None:
        self._base_url = normalize_base_url(base_url)
        self._model = model.strip()
        self._api_key = api_key.strip() if api_key is not None else None
        self._sleep = sleep
        if client is not None:
            self._client = client
        elif transport is not None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(120.0, connect=10.0),
                transport=transport,
            )
        else:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=httpx.Timeout(120.0, connect=10.0),
            )
        self._owns_client = client is None

    async def analyze(self, batch: AnalysisBatch) -> AnalysisSubmission:
        last_error: ReviewException | None = None
        for attempt in range(1, 4):
            try:
                response = await self._client.post(
                    "chat/completions" if self._base_url.endswith("/v1") else "v1/chat/completions",
                    headers=self._authorization_header(),
                    json=self._request_payload(batch),
                )
                if response.status_code >= 400:
                    raise httpx.HTTPStatusError(
                        "provider request failed",
                        request=response.request,
                        response=response,
                    )
                return self._parse_response(response)
            except httpx.TimeoutException as exc:
                last_error = provider_unavailable(
                    "Provider request failed.",
                    attempt=attempt,
                    failure_type="timeout",
                )
                if attempt >= 3:
                    raise last_error from exc
                await self._sleep(float(attempt))
            except httpx.RequestError as exc:
                raise provider_unavailable(
                    "Provider request failed.",
                    failure_type="transport",
                ) from exc
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                retriable = status == 429 or status >= 500
                last_error = provider_unavailable(
                    "Provider request failed.",
                    status=status,
                    attempt=attempt,
                    failure_type="http_status",
                )
                if not retriable or attempt >= 3:
                    raise last_error from exc
                await self._sleep(float(attempt))
            except (json.JSONDecodeError, KeyError, IndexError, TypeError, ValidationError) as exc:
                raise provider_unavailable(
                    "Provider payload was invalid.",
                    failure_type="invalid_payload",
                ) from exc
        assert last_error is not None
        raise last_error

    async def aclose(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    def _authorization_header(self) -> dict[str, str]:
        if self._api_key:
            return {"Authorization": f"Bearer {self._api_key}"}
        return {}

    def _request_payload(self, batch: AnalysisBatch) -> dict[str, Any]:
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": batch.instructions},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "run_id": batch.run_id,
                            "batch_index": batch.batch_index,
                            "requirements": [
                                item.model_dump(mode="json") for item in batch.requirements
                            ],
                            "applicable": {
                                key: [rule.model_dump(mode="json") for rule in rules]
                                for key, rules in batch.applicable.items()
                            },
                        },
                        ensure_ascii=False,
                    ),
                },
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": {
                    "name": "analysis_submission",
                    "schema": batch.analysis_submission_schema,
                },
            },
        }

    def _parse_response(self, response: httpx.Response) -> AnalysisSubmission:
        payload = response.json()
        content = payload["choices"][0]["message"]["content"]
        if not isinstance(content, str):
            raise TypeError("message content must be a string")
        return AnalysisSubmission.model_validate_json(content)