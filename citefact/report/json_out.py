"""report.json: the canonical machine-readable output.

`schema_version` is a contract: any shape change bumps it and is
documented in the changelog.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import citefact
from citefact.models import Finding, Manuscript, Source

SCHEMA_VERSION = 1


def build_report(
    manuscript: Manuscript,
    sources: dict[str, Source],
    findings: list[Finding],
    *,
    levels: list[str],
    model: str | None,
    cost_usd: float,
    duration_seconds: float,
    claims_summary: dict[str, Any] | None = None,
    partial: bool = False,
    citations: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    claims_summary = claims_summary or {}
    report = {
        "schema_version": SCHEMA_VERSION,
        "citefact_version": citefact.__version__,
        "manuscript": {
            "file": manuscript.path.name,
            "path": str(manuscript.path.resolve()),
            "sha256": manuscript.sha256,
            "words": manuscript.words,
        },
        "run": {
            "timestamp": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "model": model,
            "levels": levels,
            "cost_usd": round(cost_usd, 4),
            "duration_seconds": round(duration_seconds, 1),
            "partial": partial,
        },
        "summary": {
            "errors": sum(1 for f in findings if f.severity == "error"),
            "warnings": sum(1 for f in findings if f.severity == "warning"),
            "claims_total": claims_summary.get("claims_total", 0),
            "verdicts": claims_summary.get("verdicts", {}),
        },
        "citations": citations or [],
        "sources": [
            {
                "id": s.id, "title": s.title, "authors": s.authors, "year": s.year,
                "pdf": str(s.pdf_path) if s.pdf_path is not None else None,
                "converted": s.converted,
            }
            for s in sources.values()
        ],
        "findings": [f.to_dict() for f in findings],
    }
    return report


def write_report_json(report: dict[str, Any], out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.json"
    path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    return path
