"""Tests for the OpenAI-compatible HTTP LLM client (no network, MockTransport)."""

import json

import httpx
import pytest

from app.config import Config
from app.llm.base import LLMError
from app.llm.openai_compat import OpenAICompatLLM

BASE_URL = "https://llm.example.com"
RETRY_SUFFIX = "Return only valid JSON per schema"

VALID_RESPONSE = {
    "kind": "word",
    "canonical": "throughput",
    "translation": {"main": "пропускная способность", "notes": []},
}


def make_cfg() -> Config:
    return Config(
        api_token="t",
        db_path="db.sqlite3",
        llm_base_url=BASE_URL,
        llm_model="test-model",
        llm_api_key="secret-key",
        llm_timeout_s=10.0,
    )


def make_llm(contents: list) -> "tuple[OpenAICompatLLM, list[httpx.Request]]":
    """Build an OpenAICompatLLM serving `contents` from a MockTransport.

    Each content item is a response `content` string or an exception to
    raise inside the transport handler. Served requests are appended to
    the returned log.
    """
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        item = contents.pop(0)
        if isinstance(item, httpx.Response):
            return item
        if isinstance(item, Exception):
            raise item
        return httpx.Response(
            200, json={"choices": [{"message": {"role": "assistant", "content": item}}]}
        )

    llm = OpenAICompatLLM(make_cfg())
    llm._client = httpx.Client(
        transport=httpx.MockTransport(handler), timeout=make_cfg().llm_timeout_s
    )
    return llm, requests


def system_message(request: httpx.Request) -> str:
    return json.loads(request.content)["messages"][0]["content"]


def test_happy_path_parses_content_and_sends_expected_request():
    llm, requests = make_llm([json.dumps(VALID_RESPONSE)])

    result = llm.complete_json("Classify and translate.", "word: throughput")

    assert result == VALID_RESPONSE
    assert len(requests) == 1
    request = requests[0]
    assert str(request.url) == f"{BASE_URL}/v1/chat/completions"
    assert request.headers["Authorization"] == "Bearer secret-key"
    body = json.loads(request.content)
    assert body["model"] == "test-model"
    assert body["messages"] == [
        {"role": "system", "content": "Classify and translate."},
        {"role": "user", "content": "word: throughput"},
    ]
    assert body["response_format"] == {"type": "json_object"}


def test_invalid_json_retried_once_with_amended_prompt():
    llm, requests = make_llm(["definitely not json", json.dumps(VALID_RESPONSE)])

    result = llm.complete_json("Classify and translate.", "word: throughput")

    assert result == VALID_RESPONSE
    assert len(requests) == 2
    first_system = system_message(requests[0])
    second_system = system_message(requests[1])
    assert second_system.startswith(first_system)
    assert RETRY_SUFFIX in second_system
    assert RETRY_SUFFIX not in first_system


def test_invalid_json_twice_raises_llm_error_after_two_requests():
    llm, requests = make_llm(["garbage one", "garbage two"])

    with pytest.raises(LLMError):
        llm.complete_json("Classify and translate.", "word: throughput")

    assert len(requests) == 2


def test_json_array_is_not_a_dict_so_is_retried():
    llm, requests = make_llm(["[]", json.dumps(VALID_RESPONSE)])

    result = llm.complete_json("Classify and translate.", "word: throughput")

    assert result == VALID_RESPONSE
    assert len(requests) == 2


def test_timeout_maps_to_llm_error_without_retry():
    llm, requests = make_llm(
        [httpx.ReadTimeout("timed out after 10.0s", request=None)]
    )

    with pytest.raises(LLMError):
        llm.complete_json("Classify and translate.", "word: throughput")

    assert len(requests) == 1


def test_http_500_maps_to_llm_error_without_retry():
    llm, requests = make_llm(
        [httpx.Response(500, json={"error": {"message": "internal boom"}})]
    )

    with pytest.raises(LLMError) as excinfo:
        llm.complete_json("Classify and translate.", "word: throughput")

    assert "HTTP 500" in str(excinfo.value)
    assert "internal boom" in str(excinfo.value)
    assert len(requests) == 1


def test_http_401_maps_to_llm_error_without_retry():
    llm, requests = make_llm(
        [httpx.Response(401, text='{"error": "bad api key"}')]
    )

    with pytest.raises(LLMError) as excinfo:
        llm.complete_json("Classify and translate.", "word: throughput")

    assert "HTTP 401" in str(excinfo.value)
    assert "bad api key" in str(excinfo.value)
    assert len(requests) == 1


def test_http_200_non_json_body_maps_to_llm_error_without_retry():
    llm, requests = make_llm([httpx.Response(200, text="gateway error page")])

    with pytest.raises(LLMError) as excinfo:
        llm.complete_json("Classify and translate.", "word: throughput")

    assert "HTTP 200" in str(excinfo.value)
    assert "gateway error page" in str(excinfo.value)
    assert len(requests) == 1


@pytest.mark.parametrize(
    "body",
    [
        {"id": "x"},
        {"choices": []},
        {"choices": [{"message": {}}]},
        {"choices": [{"message": {"role": "assistant"}}]},
        {"choices": "no list"},
    ],
)
def test_http_200_malformed_json_body_maps_to_llm_error_without_retry(body):
    llm, requests = make_llm([httpx.Response(200, json=body)])

    with pytest.raises(LLMError) as excinfo:
        llm.complete_json("Classify and translate.", "word: throughput")

    assert "HTTP 200" in str(excinfo.value)
    assert len(requests) == 1
