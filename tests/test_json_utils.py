import pytest

from citefact.llm.json_utils import parse_llm_json_response, repair_json, strip_fences


def test_trailing_comma():
    assert repair_json('{"a": 1,}') == {"a": 1}


def test_unbalanced_braces():
    assert repair_json('{"a": {"b": 1}') == {"a": {"b": 1}}


def test_embedded_quotes_escaped():
    assert repair_json('{"e": "she said "hi" loudly"}') == {"e": 'she said "hi" loudly'}


def test_strip_fences():
    assert strip_fences('```json\n{"a": 1}\n```') == '{"a": 1}'


def test_parse_falls_back_to_llm_retry(monkeypatch):
    class R:  # minimal LlmResult stand-in
        text = '{"fixed": true}'
    monkeypatch.setattr("citefact.llm.client.call_llm", lambda *a, **k: R())
    out = parse_llm_json_response("{totally broken", messages=[], model="m")
    assert out == {"fixed": True}


def test_parse_repair_retry_reports_cost_to_sink(monkeypatch):
    class R:  # minimal LlmResult stand-in, now carrying a cost
        text = '{"fixed": true}'
        cost_usd = 0.03
    monkeypatch.setattr("citefact.llm.client.call_llm", lambda *a, **k: R())
    sink: list[float] = []
    out = parse_llm_json_response("{totally broken", messages=[], model="m", cost_sink=sink)
    assert out == {"fixed": True}
    assert sink == [0.03]
