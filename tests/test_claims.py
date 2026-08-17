import json
from pathlib import Path

import pytest

from citefact.checks.claims import check_claims
from citefact.citations.base import Citation, MatchedCitation
from citefact.llm.client import LlmResult
from citefact.models import Source

TEXT = "Smith (2023) found that most participants preferred assistance."


def _matched():
    return [MatchedCitation(Citation("Smith (2023)", "Smith", 2023, 0, 12), "smith2023", 0.95)]


def _sources():
    return {"smith2023": Source(
        id="smith2023", title="T", authors="Smith, J.", year=2023,
        content_hash="abc123", text="Participants preferred assistance in 78% of trials.",
    )}


def _mock_llm(monkeypatch, extraction: dict, verdict: dict):
    calls = []

    def fake(messages, *, model, temperature=0.2, max_tokens=8192):
        calls.append(messages)
        payload = extraction if "PRE-RESOLVED" in json.dumps(messages, default=str) else verdict
        return LlmResult(text=json.dumps(payload), model=model, cost_usd=0.01, finish_reason="stop")

    monkeypatch.setattr("citefact.checks.claims.call_llm", fake)
    return calls


EXTRACTION = {"claims": [{"claim": TEXT, "author": "Smith", "year": "2023",
                          "paper_id": "smith2023", "key_numbers": [], "manuscript_location": "p1"}],
              "unmatched_citations": [], "summary": "1 claim"}
VERDICT = {"verdict": "supported", "confidence": 92,
           "evidence": "Participants preferred assistance in 78% of trials.",
           "source_location": "Results", "reasoning": ["Direct match."]}


def test_supported_claim_is_info_finding(tmp_path, monkeypatch):
    _mock_llm(monkeypatch, EXTRACTION, VERDICT)
    findings, summary = check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
    assert len(findings) == 1
    f = findings[0]
    assert f.type == "claim_verdict" and f.severity == "info"
    assert f.details["verdict"] == "supported" and f.details["confidence"] == 92
    assert summary["claims_total"] == 1 and summary["verdicts"]["supported"] == 1
    assert summary["cost_usd"] == pytest.approx(0.02)


def test_misrepresented_is_error(tmp_path, monkeypatch):
    _mock_llm(monkeypatch, EXTRACTION, {**VERDICT, "verdict": "misrepresented"})
    findings, _ = check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
    assert findings[0].severity == "error"


def test_verdict_cache_hit_skips_llm(tmp_path, monkeypatch):
    calls = _mock_llm(monkeypatch, EXTRACTION, VERDICT)
    check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
    n_first = len(calls)
    _, summary = check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
    # Second run: extraction AND verdict both come from cache (zero LLM
    # calls); the call count alone can't tell a cache hit apart from a
    # regression that extracts zero claims, so also assert the verdict was
    # actually populated from the cached file.
    assert len(calls) == n_first
    assert summary["claims_total"] == 1
    assert summary["verdicts"]["supported"] == 1


def test_extraction_failure_degrades_gracefully(tmp_path, monkeypatch):
    def fake(messages, *, model, temperature=0.3, max_tokens=16384):
        raise RuntimeError("provider outage")

    monkeypatch.setattr("citefact.checks.claims.call_llm", fake)
    findings, summary = check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
    assert findings == []
    assert summary["claims_total"] == 0
    assert summary["partial"] is True
    assert "error" in summary


def test_corrupt_verdict_cache_entry_is_treated_as_miss(tmp_path, monkeypatch):
    calls = _mock_llm(monkeypatch, EXTRACTION, VERDICT)
    check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
    cache_files = list((tmp_path / "cache" / "verdicts").glob("*.json"))
    assert len(cache_files) == 1
    cache_files[0].write_text("{not valid json at all", encoding="utf-8")
    n_before = len(calls)

    findings, summary = check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)

    # extraction comes from its own cache (0 calls); only the poisoned
    # verdict is re-fetched from the LLM (+1 call) rather than poisoning
    # the whole run.
    assert len(calls) == n_before + 1
    assert findings[0].details["verdict"] == "supported"
    assert json.loads(cache_files[0].read_text(encoding="utf-8"))["verdict"] == "supported"


def test_repair_retry_cost_is_added_to_summary(tmp_path, monkeypatch):
    """The one-shot JSON-repair retry (json_utils's third seatbelt) makes
    its own billed LLM call; that cost must not be silently dropped."""
    def fake_primary(messages, *, model, temperature=0.3, max_tokens=16384):
        if "PRE-RESOLVED" in json.dumps(messages, default=str):
            return LlmResult(text=json.dumps(EXTRACTION), model=model,
                              cost_usd=0.01, finish_reason="stop")
        # Verdict call: return invalid JSON to force the repair-retry path.
        return LlmResult(text="{not valid json", model=model,
                          cost_usd=0.01, finish_reason="stop")

    def fake_repair(messages, *, model, temperature=0.0):
        return LlmResult(text=json.dumps(VERDICT), model=model,
                          cost_usd=0.05, finish_reason="stop")

    monkeypatch.setattr("citefact.checks.claims.call_llm", fake_primary)
    monkeypatch.setattr("citefact.llm.client.call_llm", fake_repair)

    findings, summary = check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)

    assert findings[0].details["verdict"] == "supported"
    # extraction (0.01) + verdict primary call (0.01) + repair retry (0.05)
    assert summary["cost_usd"] == pytest.approx(0.07)


def test_llm_failure_yields_unverified(tmp_path, monkeypatch):
    calls = []

    def fake(messages, *, model, temperature=0.2, max_tokens=8192):
        calls.append(messages)
        if len(calls) == 1:
            return LlmResult(json.dumps(EXTRACTION), model, 0.01, "stop")
        raise RuntimeError("provider down")

    monkeypatch.setattr("citefact.checks.claims.call_llm", fake)
    findings, summary = check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
    assert findings[0].details["verdict"] == "unverified"
    assert findings[0].severity == "warning"
    assert summary["partial"] is True


@pytest.mark.evals
def test_eval_supported_and_misrepresented_verdicts():
    """Real LLM call: two labeled claim/source pairs. Costs money."""
    from citefact.checks.claims import suggest_verdict
    from citefact.llm.client import resolve_model

    model = resolve_model(None, None)
    source = ("We surveyed 120 clinicians. 78% reported that AI assistance "
              "improved their diagnostic confidence, although accuracy was unchanged.")
    supported = suggest_verdict(
        "Smith (2023) found that 78% of clinicians reported improved "
        "diagnostic confidence with AI assistance.", source, ["78%"], model=model)
    assert supported["verdict"] == "supported"
    misrepresented = suggest_verdict(
        "Smith (2023) demonstrated that AI assistance improves diagnostic "
        "accuracy.", source, None, model=model)
    assert misrepresented["verdict"] in ("misrepresented", "partial")


def test_verify_events_carry_counts_and_running_cost(tmp_path, monkeypatch):
    from citefact.progress import ProgressEvent

    _mock_llm(monkeypatch, EXTRACTION, VERDICT)
    events: list[ProgressEvent] = []
    check_claims(TEXT, _matched(), _sources(), model="m",
                 cache_dir=tmp_path, progress=events.append)
    verify = [e for e in events if e.phase == "verify" and e.total is not None]
    assert len(verify) == 1
    assert (verify[0].current, verify[0].total) == (1, 1)
    assert verify[0].cost_usd is not None and verify[0].cost_usd > 0


class TestExtractionCache:
    def test_second_run_skips_extraction_llm_call(self, tmp_path, monkeypatch):
        """Claim extraction is deterministic in its inputs (manuscript text,
        catalog, resolved citations, model, prompt version) but the LLM is
        not: uncached extraction makes reports cost money and vary between
        runs on identical input. Cache it like verdicts."""
        calls = _mock_llm(monkeypatch, EXTRACTION, VERDICT)
        check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
        n_first = len(calls)
        findings, summary = check_claims(TEXT, _matched(), _sources(),
                                         model="m", cache_dir=tmp_path)
        assert len(calls) == n_first          # zero LLM calls on the second run
        assert summary["claims_total"] == 1   # claims still come through
        assert summary["cost_usd"] == 0.0

    def test_text_change_invalidates_extraction(self, tmp_path, monkeypatch):
        calls = _mock_llm(monkeypatch, EXTRACTION, VERDICT)
        check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
        n_first = len(calls)
        check_claims(TEXT + " Edited.", _matched(), _sources(),
                     model="m", cache_dir=tmp_path)
        assert len(calls) > n_first           # re-extracts on changed text

    def test_failed_extraction_is_not_cached(self, tmp_path, monkeypatch):
        calls = []

        def fake(messages, *, model, temperature=0.2, max_tokens=8192):
            calls.append(messages)
            raise RuntimeError("provider down")

        monkeypatch.setattr("citefact.checks.claims.call_llm", fake)
        check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
        check_claims(TEXT, _matched(), _sources(), model="m", cache_dir=tmp_path)
        assert len(calls) == 2                # both runs attempted extraction
