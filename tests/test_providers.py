from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import httpx
import pytest
import respx

from requirements_review_agent.analysis import AnalysisBatch, build_analysis_batch
from requirements_review_agent.errors import PROVIDER_UNAVAILABLE, ReviewException
from requirements_review_agent.models import (
    AnalysisSubmission,
    ApplicableRule,
    AtomicRequirement,
    Impact,
    ProviderMode,
    SourceRef,
)
from requirements_review_agent.providers import (
    AnalysisProvider,
    OpenAICompatibleProvider,
    build_provider,
)


def source(page: int = 1, quote: str = "Quote") -> SourceRef:
    return SourceRef(page=page, quote=quote, section="1.0")


def requirement(requirement_id: str = "REQ-1") -> AtomicRequirement:
    return AtomicRequirement(
        requirement_id=requirement_id,
        text=f"Requirement {requirement_id}",
        sources=(source(),),
    )


def rule(rule_id: str = "acceptance.criteria") -> ApplicableRule:
    return ApplicableRule(
        rule_id=rule_id,
        question=f"请确认 {rule_id} 是否满足？",
        weight=1,
        impact=Impact.BOTH,
        scenario_category="baseline",
        always=True,
        keywords=(),
    )


def batch() -> AnalysisBatch:
    req = requirement()
    return build_analysis_batch(
        run_id="run-123",
        batch_index=0,
        requirements=(req,),
        applicable={req.requirement_id: (rule(),)},
    )


def valid_submission_json() -> str:
    return AnalysisSubmission(
        schema_version="1.0",
        requirements=(),
    ).model_dump_json()


def openai_response(content: str) -> dict[str, Any]:
    return {
        "choices": [
            {
                "message": {
                    "content": content,
                }
            }
        ]
    }


class FakeProvider(AnalysisProvider):
    def __init__(self, submission: AnalysisSubmission) -> None:
        self.submission = submission

    async def analyze(self, batch: AnalysisBatch) -> AnalysisSubmission:
        return self.submission


async def no_sleep(_: float) -> None:
    return None


def provider(
    *,
    base_url: str = "https://approved.example",
    model: str = "approved-model",
    api_key: str | None = "secret-key",
    transport: httpx.AsyncBaseTransport | None = None,
    sleep: Callable[[float], Awaitable[None]] = no_sleep,
) -> OpenAICompatibleProvider:
    return OpenAICompatibleProvider(
        base_url=base_url,
        model=model,
        api_key=api_key,
        transport=transport,
        sleep=sleep,
    )


async def test_copilot_mode_returns_no_server_side_provider() -> None:
    assert build_provider(ProviderMode.COPILOT, {}) is None


def test_external_mode_requires_explicit_endpoint_and_env_key() -> None:
    with pytest.raises(ReviewException, match=PROVIDER_UNAVAILABLE):
        build_provider(ProviderMode.COMPANY_API, {})


def test_local_mode_requires_non_empty_base_url_and_model() -> None:
    with pytest.raises(ReviewException, match=PROVIDER_UNAVAILABLE):
        build_provider(
            ProviderMode.LOCAL,
            {
                "RRA_LOCAL_BASE_URL": "",
                "RRA_LOCAL_MODEL": "approved-model",
            },
        )


@pytest.mark.asyncio
async def test_local_mode_ignores_unconfigured_api_key_variable() -> None:
    authorization: str | None = "not-called"

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal authorization
        authorization = request.headers.get("authorization")
        return httpx.Response(200, json=openai_response(valid_submission_json()))

    configured = build_provider(
        ProviderMode.LOCAL,
        {
            "RRA_LOCAL_BASE_URL": "http://localhost:11434",
            "RRA_LOCAL_MODEL": "local-model",
            "RRA_LOCAL_API_KEY": "not-a-supported-setting",
        },
        transport=httpx.MockTransport(handler),
    )

    assert isinstance(configured, OpenAICompatibleProvider)
    await configured.analyze(batch())
    assert authorization is None


def test_company_mode_requires_api_key() -> None:
    with pytest.raises(ReviewException, match=PROVIDER_UNAVAILABLE):
        build_provider(
            ProviderMode.COMPANY_API,
            {
                "RRA_COMPANY_BASE_URL": "https://approved.example",
                "RRA_COMPANY_MODEL": "approved-model",
            },
        )


@pytest.mark.asyncio
async def test_adapter_parses_schema_response(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post("https://approved.example/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=openai_response(valid_submission_json()))
    )

    result = await provider().analyze(batch())

    assert route.called
    assert result.schema_version == "1.0"


@pytest.mark.asyncio
async def test_adapter_preserves_base_url_v1_prefix_without_leading_slash_bug(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post("https://approved.example/v1/chat/completions").mock(
        return_value=httpx.Response(200, json=openai_response(valid_submission_json()))
    )

    result = await provider(base_url="https://approved.example/v1").analyze(batch())

    assert route.called
    assert result.schema_version == "1.0"


@pytest.mark.asyncio
async def test_adapter_sends_schema_payload_and_auth_header(respx_mock: respx.MockRouter) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        captured["json"] = request.read().decode("utf-8")
        return httpx.Response(200, json=openai_response(valid_submission_json()))

    respx_mock.post("https://approved.example/v1/chat/completions").mock(side_effect=handler)

    await provider().analyze(batch())

    assert captured["authorization"] == "Bearer secret-key"
    assert '"response_format"' in captured["json"]
    assert '"json_schema"' in captured["json"]


@pytest.mark.asyncio
async def test_adapter_omits_auth_header_when_key_missing(respx_mock: respx.MockRouter) -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["authorization"] = request.headers.get("authorization")
        return httpx.Response(200, json=openai_response(valid_submission_json()))

    respx_mock.post("https://approved.example/v1/chat/completions").mock(side_effect=handler)

    await provider(api_key=None).analyze(batch())

    assert captured["authorization"] is None


@pytest.mark.asyncio
async def test_adapter_retries_timeouts_with_injected_delays() -> None:
    attempts = 0
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts < 3:
            raise httpx.ReadTimeout("timed out", request=request)
        return httpx.Response(200, json=openai_response(valid_submission_json()))

    transport = httpx.MockTransport(handler)

    result = await provider(transport=transport, sleep=sleep).analyze(batch())

    assert result.schema_version == "1.0"
    assert attempts == 3
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_adapter_retries_429_and_5xx_only() -> None:
    statuses = [429, 503, 200]
    delays: list[float] = []

    async def sleep(delay: float) -> None:
        delays.append(delay)

    async def handler(_: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        if status == 200:
            return httpx.Response(200, json=openai_response(valid_submission_json()))
        return httpx.Response(status, json={"error": {"message": "retry"}})

    result = await provider(transport=httpx.MockTransport(handler), sleep=sleep).analyze(batch())

    assert result.schema_version == "1.0"
    assert delays == [1, 2]


@pytest.mark.asyncio
async def test_adapter_does_not_retry_400() -> None:
    attempts = 0

    async def handler(_: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        return httpx.Response(400, json={"error": {"message": "bad request"}})

    with pytest.raises(ReviewException, match=PROVIDER_UNAVAILABLE):
        await provider(transport=httpx.MockTransport(handler)).analyze(batch())

    assert attempts == 1


@pytest.mark.asyncio
async def test_adapter_invalid_json_is_provider_unavailable_without_secrets() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"choices": [{"message": {"content": "not-json"}}]})

    with pytest.raises(ReviewException, match=PROVIDER_UNAVAILABLE) as exc_info:
        await provider(
            api_key="super-secret",
            transport=httpx.MockTransport(handler),
        ).analyze(batch())

    message = str(exc_info.value)
    assert "super-secret" not in message
    assert "authorization" not in message.lower()


@pytest.mark.asyncio
async def test_adapter_reports_sanitized_details_for_http_failure() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": {"message": "down"}})

    with pytest.raises(ReviewException, match=PROVIDER_UNAVAILABLE) as exc_info:
        await provider(transport=httpx.MockTransport(handler)).analyze(batch())

    assert exc_info.value.error.details == {"status": 503, "attempt": 3, "type": "http_status"}


@pytest.mark.asyncio
async def test_adapter_wraps_non_timeout_transport_error_without_retry() -> None:
    attempts = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ConnectError("secret endpoint detail", request=request)

    with pytest.raises(ReviewException, match=PROVIDER_UNAVAILABLE) as exc_info:
        await provider(transport=httpx.MockTransport(handler)).analyze(batch())

    assert attempts == 1
    assert exc_info.value.error.details == {"type": "transport"}
    assert "secret endpoint detail" not in str(exc_info.value)


@pytest.mark.asyncio
async def test_adapter_closes_owned_client() -> None:
    configured = provider(transport=httpx.MockTransport(lambda _: httpx.Response(200)))

    await configured.aclose()

    assert configured._client.is_closed


@pytest.mark.asyncio
async def test_adapter_does_not_close_injected_client() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200)))
    configured = OpenAICompatibleProvider(
        base_url="https://approved.example",
        model="approved-model",
        api_key=None,
        client=client,
    )

    await configured.aclose()

    assert not client.is_closed
    await client.aclose()