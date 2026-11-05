import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from app.llm.base import LLMQueryResponse
from app.llm.fake import FakeLLM

FIXTURES_DIR = Path(__file__).parent / "fixtures" / "llm"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES_DIR / f"{name}.json").read_text(encoding="utf-8"))


def test_word_fixture_is_valid_response():
    data = load_fixture("word")
    resp = LLMQueryResponse.model_validate(data)
    assert resp.kind == "word"
    assert resp.canonical == "throughput"
    assert resp.translation.main == "пропускная способность"
    assert len(resp.translation.notes) == 1


def test_sentence_fixture_is_valid_response():
    data = load_fixture("sentence")
    resp = LLMQueryResponse.model_validate(data)
    assert resp.kind == "sentence"
    assert resp.canonical is None
    assert resp.translation.main == "цельный перевод"
    assert resp.translation.notes == []


def test_canonical_required_for_word():
    with pytest.raises(ValidationError):
        LLMQueryResponse.model_validate(
            {"kind": "word", "canonical": None,
             "translation": {"main": "x", "notes": []}}
        )


def test_canonical_required_for_phrase():
    with pytest.raises(ValidationError):
        LLMQueryResponse.model_validate(
            {"kind": "phrase", "canonical": None,
             "translation": {"main": "x", "notes": []}}
        )


def test_paragraph_fixture_is_valid_response():
    data = load_fixture("paragraph")
    resp = LLMQueryResponse.model_validate(data)
    assert resp.kind == "paragraph"
    assert resp.canonical is None
    assert resp.translation.main == "цельный перевод абзаца с сохранением смысла"
    assert resp.translation.notes == []


def test_fragment_fixture_is_valid_response():
    data = load_fixture("fragment")
    resp = LLMQueryResponse.model_validate(data)
    assert resp.kind == "fragment"
    assert resp.canonical is None
    assert resp.translation.main == "посреди"
    assert resp.translation.notes == []


def test_none_canonical_allowed_for_fragment():
    resp = LLMQueryResponse.model_validate(
        {"kind": "fragment", "canonical": None,
         "translation": {"main": "x", "notes": []}}
    )
    assert resp.canonical is None


def test_fake_llm_routes_paragraph_and_fragment_markers():
    fake = FakeLLM(
        {
            "__PARAGRAPH_CASE__": load_fixture("paragraph"),
            "__FRAGMENT_CASE__": load_fixture("fragment"),
        }
    )
    assert fake.complete_json("system", "Case: __PARAGRAPH_CASE__") == load_fixture(
        "paragraph"
    )
    assert fake.calls == 1
    assert fake.complete_json("system", "Case: __FRAGMENT_CASE__") == load_fixture(
        "fragment"
    )
    assert fake.calls == 2


def test_none_canonical_allowed_for_sentence():
    resp = LLMQueryResponse.model_validate(
        {"kind": "sentence", "canonical": None,
         "translation": {"main": "x", "notes": []}}
    )
    assert resp.canonical is None


def test_unknown_kind_rejected():
    with pytest.raises(ValidationError):
        LLMQueryResponse.model_validate(
            {"kind": "poem", "canonical": None,
             "translation": {"main": "x", "notes": []}}
        )


def test_fake_llm_routes_by_marker():
    fake = FakeLLM(
        {
            "__WORD_CASE__": load_fixture("word"),
            "__SENTENCE_CASE__": load_fixture("sentence"),
        }
    )
    assert fake.complete_json("system", "Case: __WORD_CASE__") == load_fixture("word")
    assert fake.calls == 1
    assert fake.complete_json(
        "system", "Case: __SENTENCE_CASE__"
    ) == load_fixture("sentence")
    assert fake.calls == 2


def test_fake_llm_unknown_case_raises():
    fake = FakeLLM({"__WORD_CASE__": load_fixture("word")})
    with pytest.raises(KeyError):
        fake.complete_json("system", "no marker in this user prompt")
