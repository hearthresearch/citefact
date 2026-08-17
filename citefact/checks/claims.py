"""Claims check: claim-source verification via LLM (claim extraction +
per-claim verdicts), built on citefact's client, JSON seatbelts, and verdict cache."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, Callable, Optional

from citefact.citations.base import MatchedCitation
from citefact.llm.client import call_llm, supports_prompt_caching
from citefact.llm.json_utils import parse_llm_json_response
from citefact.progress import ProgressEvent, ProgressFn
from citefact.llm.prompt_loader import load_system_prompt, render_user_prompt
from citefact.models import Finding, Source

log = logging.getLogger(__name__)

PROMPT_VERSION = 1  # bump on any prompt change: invalidates the verdict cache
MAX_MANUSCRIPT_CHARS = 50_000
MAX_SOURCE_CHARS = 40_000
VALID_VERDICTS = ("supported", "partial", "misrepresented", "not_in_paper")
_SEVERITY = {"supported": "info", "partial": "warning",
             "misrepresented": "error", "not_in_paper": "error",
             "unverified": "warning"}


def _first_author_surname(authors: str) -> str:
    """Extract the first author's surname, robust to both stored formats.

    - BibTeX-ish: "Simkute A., Surana A., Luger E., ..."  → "Simkute"
    - Zotero API: "Bornmann Lutz, Mutz Rüdiger"           → "Bornmann"
    - BibTeX comma-inverted: "Smith, J., Jones, K."       → "Smith"
      (split on the first comma; the single surviving token is the surname)
    - Single-author initials: "Hildt E."                  → "Hildt"

    The previous implementation took `split(",")[0].split()[-1]`, which
    returned the initial ("A.") for the first two formats and the firstname
    ("Lutz") for Zotero — every paper got a wrong surname, leaving the
    LLM to match citations by title alone.
    """
    if not authors:
        return ""
    first_entry = authors.split(",", 1)[0].strip()
    if not first_entry:
        return ""
    tokens = first_entry.split()
    # Drop initial tokens like "A.", "R.L.", "M.D." — short with a trailing dot.
    non_initials = [t for t in tokens if not (len(t) <= 4 and t.endswith("."))]
    return non_initials[0] if non_initials else tokens[0]


def _recover_truncated_claims(response_text: str) -> Optional[dict]:
    """Try to recover claims from a truncated JSON response.

    When the LLM hits max output tokens, the JSON is cut off mid-way.
    This function extracts any complete claim objects from the partial JSON.
    """
    # Find the claims array boundaries
    claims_match = re.search(r'"claims"\s*:\s*\[', response_text)
    if not claims_match:
        return None

    # Find where unmatched_citations starts (end boundary for claims array)
    um_match = re.search(r'"unmatched_citations"\s*:\s*\[', response_text)
    claims_end = um_match.start() if um_match else len(response_text)
    claims_text = response_text[claims_match.end():claims_end]

    # Extract individual claim objects using regex
    claim_pattern = re.compile(r'\{[^{}]*"claim"\s*:\s*"[^"]*"[^{}]*\}', re.DOTALL)
    claims = []
    for raw in claim_pattern.findall(claims_text):
        try:
            claim = json.loads(raw)
            claim.setdefault("claim", "")
            claim.setdefault("author", "")
            claim.setdefault("year", "")
            claim.setdefault("paper_id", None)
            claim.setdefault("key_numbers", [])
            claim.setdefault("manuscript_location", "")
            claims.append(claim)
        except json.JSONDecodeError:
            continue

    if not claims:
        return None

    # Try to extract unmatched_citations too
    unmatched = []
    if um_match:
        um_text = response_text[um_match.end():]
        um_pattern = re.compile(r'\{[^{}]*"author"\s*:\s*"[^"]*"[^{}]*\}', re.DOTALL)
        for raw in um_pattern.findall(um_text):
            try:
                unmatched.append(json.loads(raw))
            except json.JSONDecodeError:
                continue

    return {"claims": claims, "unmatched_citations": unmatched}


def extract_claims(
    text: str,
    catalog: dict[str, dict],
    matched: list[MatchedCitation],
    *,
    model: str,
) -> dict[str, Any]:
    resolved = [
        {"span": m.citation.raw, "author": m.citation.author_string,
         "year": m.citation.year, "paper_id": m.paper_id,
         "confidence": round(m.confidence, 2)}
        for m in matched
    ]
    catalog_info = [
        {"paper_id": pid, "first_author_lastname": _first_author_surname(p.get("authors", "")),
         "year": p.get("year", "")}
        for pid, p in catalog.items()
    ]
    if len(text) > MAX_MANUSCRIPT_CHARS:
        text = text[:MAX_MANUSCRIPT_CHARS] + "\n\n[... truncated for length ...]"

    messages = [
        {"role": "system", "content": load_system_prompt("claims_extract_system")},
        {"role": "user", "content": render_user_prompt(
            "claims_extract_user",
            corpus_info=json.dumps(catalog_info, indent=2),
            manuscript_text=text,
            resolved_citations=json.dumps(resolved, indent=2),
            citation_style="apa",
        )},
    ]
    result = call_llm(messages, model=model, temperature=0.3, max_tokens=16384)
    truncated = result.finish_reason == "length"
    if truncated:
        # Max-token exhaustion means truncation, not corruption: no JSON
        # repair can fix it. Recover complete claim objects instead.
        recovered = _recover_truncated_claims(result.text)
        data = recovered if recovered else {"claims": [], "unmatched_citations": []}
        repair_cost: list[float] = []
    else:
        repair_cost = []
        data = parse_llm_json_response(
            result.text, messages=messages, model=model, cost_sink=repair_cost,
        )
    claims = data.get("claims", [])
    for claim in claims:
        claim.setdefault("claim", "")
        claim.setdefault("author", "")
        claim.setdefault("year", "")
        claim.setdefault("paper_id", None)
        claim.setdefault("key_numbers", [])
        claim.setdefault("manuscript_location", "")
    return {"claims": claims,
            "unmatched_citations": data.get("unmatched_citations", []),
            "cost_usd": result.cost_usd + sum(repair_cost), "truncated": truncated}


def suggest_verdict(
    claim_text: str,
    source_text: str,
    key_numbers: list[str] | None,
    *,
    model: str,
) -> dict[str, Any]:
    if len(source_text) > MAX_SOURCE_CHARS:
        source_text = source_text[:MAX_SOURCE_CHARS] + "\n\n[... truncated for length ...]"
    numbers_context = f"\nKEY NUMBERS TO VERIFY: {', '.join(key_numbers)}" if key_numbers else ""
    paper_block: Any = f"SOURCE PAPER CONTENT:\n{source_text}"
    instructions = render_user_prompt(
        "claims_verdict_user_instructions",
        claim_text=claim_text, numbers_context=numbers_context,
    )
    if supports_prompt_caching(model):
        # One source carries N claims; cache the big paper block so claims
        # 2..N only pay for the instructions.
        user_content: Any = [
            {"type": "text", "text": paper_block, "cache_control": {"type": "ephemeral"}},
            {"type": "text", "text": instructions},
        ]
    else:
        user_content = f"{paper_block}\n\n{instructions}"

    messages = [
        {"role": "system", "content": load_system_prompt("claims_verdict_system")},
        {"role": "user", "content": user_content},
    ]
    result = call_llm(messages, model=model, temperature=0.2)
    repair_cost: list[float] = []
    data = parse_llm_json_response(
        result.text, messages=messages, model=model, cost_sink=repair_cost,
    )

    raw_verdict = data.get("verdict")
    verdict = str(raw_verdict).lower() if raw_verdict is not None else None
    if verdict not in VALID_VERDICTS:
        verdict = None  # model abstained or answered off-schema: needs human review
    try:
        confidence = max(0, min(100, int(data.get("confidence", 50))))
    except (TypeError, ValueError):
        confidence = 50
    reasoning = data.get("reasoning", [])
    if isinstance(reasoning, list):
        reasoning = " ".join(str(r) for r in reasoning)
    return {"verdict": verdict, "confidence": confidence,
            "evidence": data.get("evidence", ""),
            "source_location": data.get("source_location", ""),
            "reasoning": reasoning, "cost_usd": result.cost_usd + sum(repair_cost)}


def _verdict_cache_key(claim_text: str, content_hash: str | None, model: str) -> str:
    raw = f"{claim_text}\n{content_hash}\n{model}\n{PROMPT_VERSION}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def check_claims(
    text: str,
    matched: list[MatchedCitation],
    sources: dict[str, Source],
    *,
    model: str,
    cache_dir: Path,
    progress: ProgressFn | None = None,
) -> tuple[list[Finding], dict[str, Any]]:
    say: ProgressFn = progress or (lambda _event: None)
    catalog = {sid: {"authors": s.authors, "year": s.year} for sid, s in sources.items()}

    # Extraction is cached like verdicts: the LLM pass is expensive and
    # non-deterministic, so re-running on identical inputs would cost money
    # and make reports vary between runs. The key covers everything that
    # shapes the extraction prompt.
    extraction_dir = cache_dir / "cache" / "extractions"
    extraction_dir.mkdir(parents=True, exist_ok=True)
    key_material = json.dumps(
        {
            "text": text,
            "model": model,
            "prompt_version": PROMPT_VERSION,
            "catalog": catalog,
            "resolved": [
                [m.citation.raw, m.citation.author_string, str(m.citation.year), m.paper_id]
                for m in matched
            ],
        },
        sort_keys=True, ensure_ascii=False,
    )
    extraction_file = extraction_dir / (
        hashlib.sha256(key_material.encode("utf-8")).hexdigest() + ".json"
    )

    extraction: dict[str, Any] | None = None
    extraction_error: str | None = None
    if extraction_file.exists():
        try:
            cached = json.loads(extraction_file.read_text(encoding="utf-8"))
            extraction = {**cached, "cost_usd": 0.0, "truncated": False}
        except (json.JSONDecodeError, OSError) as exc:
            log.warning("Corrupt extraction cache entry %s: %s", extraction_file, exc)
            extraction_file.unlink(missing_ok=True)
    if extraction is None:
        say(ProgressEvent(phase="extract", message="Extracting claims (one LLM pass)..."))
        try:
            extraction = extract_claims(text, catalog, matched, model=model)
        except Exception as exc:
            # Any provider/parsing failure here (missing API key, outage, a
            # final JSONDecodeError from the repair retry) must degrade rather
            # than propagate: the LLM level is best-effort and must never
            # discard deterministic findings that already completed. Failures
            # and truncated output are never cached.
            log.warning("Claim extraction failed; degrading to zero claims: %s", exc)
            extraction = {"claims": [], "unmatched_citations": [], "cost_usd": 0.0, "truncated": False}
            extraction_error = str(exc)
        else:
            if not extraction["truncated"]:
                extraction_file.write_text(
                    json.dumps({
                        "claims": extraction["claims"],
                        "unmatched_citations": extraction["unmatched_citations"],
                    }, ensure_ascii=False),
                    encoding="utf-8",
                )
    cost = extraction["cost_usd"]
    partial = extraction["truncated"] or extraction_error is not None

    # Group claims by source; only verifiable sources (converted text) proceed.
    by_source: dict[str, list[dict]] = {}
    for claim in extraction["claims"]:
        pid = claim.get("paper_id")
        if pid is not None and pid in sources and sources[pid].text is not None:
            by_source.setdefault(pid, []).append(claim)

    verdict_dir = cache_dir / "cache" / "verdicts"
    verdict_dir.mkdir(parents=True, exist_ok=True)

    findings: list[Finding] = []
    verdict_counts: dict[str, int] = {}
    total = sum(len(v) for v in by_source.values())
    done = 0
    for pid, claims in by_source.items():
        source = sources[pid]
        for claim in claims:
            done += 1
            say(ProgressEvent(
                phase="verify", message=pid,
                current=done, total=total, cost_usd=round(cost, 2),
            ))
            key = _verdict_cache_key(claim["claim"], source.content_hash, model)
            cache_file = verdict_dir / f"{key}.json"
            data = None
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError) as exc:
                    log.warning("Corrupt verdict cache entry %s: %s", cache_file, exc)
                    cache_file.unlink(missing_ok=True)
            if data is None:
                try:
                    data = suggest_verdict(
                        claim["claim"], source.text, claim.get("key_numbers"), model=model,
                    )
                    cost += data.pop("cost_usd", 0.0)
                    cache_file.write_text(json.dumps(data), encoding="utf-8")
                except Exception as exc:
                    log.warning("Verdict failed for a claim against %s: %s", pid, exc)
                    partial = True
                    data = {"verdict": None, "confidence": 0, "evidence": "",
                            "source_location": "", "reasoning": "", "error": str(exc)}
            verdict = data["verdict"] if data.get("verdict") is not None else "unverified"
            verdict_counts[verdict] = verdict_counts.get(verdict, 0) + 1
            details = {
                "source_id": pid, "claim": claim["claim"], "verdict": verdict,
                "evidence": data.get("evidence", ""),
                "reasoning": data.get("reasoning", ""),
                "confidence": data.get("confidence", 0),
            }
            if data.get("error") is not None:
                details["error"] = data["error"]
            findings.append(Finding(
                level="claims", type="claim_verdict",
                severity=_SEVERITY[verdict], details=details,
            ))

    summary = {
        "claims_total": total,
        "verdicts": verdict_counts,
        "cost_usd": round(cost, 4),
        "partial": partial,
    }
    if extraction_error is not None:
        summary["error"] = extraction_error
    return findings, summary
