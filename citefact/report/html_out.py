"""Self-contained HTML report. One file, inline CSS/JS, no network."""

from __future__ import annotations

import difflib
import html
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader

_TEMPLATES = Path(__file__).parent / "templates"
_env = Environment(
    loader=FileSystemLoader(_TEMPLATES),
    # Single-template environment (only report.html.j2); autoescape must be
    # unconditional. `select_autoescape` decides by filename, and
    # "report.html.j2" does not end in ".html", so it silently disabled
    # escaping for every render, making every manuscript/source/LLM string
    # interpolated via {{ }} a stored-XSS point. Never switch this back to
    # filename-based selection without renaming the template to end in
    # ".html".
    autoescape=True,
)


def word_diff_html(a: str, b: str) -> str:
    """Word-level diff of quote (a) vs closest source match (b)."""
    out: list[str] = []
    aw, bw = a.split(), b.split()
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, aw, bw).get_opcodes():
        if op == "equal":
            out.append(html.escape(" ".join(aw[i1:i2])))
        else:
            if i1 != i2:
                out.append(f"<del>{html.escape(' '.join(aw[i1:i2]))}</del>")
            if j1 != j2:
                out.append(f"<ins>{html.escape(' '.join(bw[j1:j2]))}</ins>")
    return " ".join(out)


def write_report_html(report: dict[str, Any], out_dir: Path) -> Path:
    # Work on copies of the finding dicts: this function must never mutate
    # the caller's report dict (e.g. the CLI may still serialize it to
    # `report.json` / stream it via `--json` after this call).
    findings = report["findings"]
    by_level = {
        "citations": [dict(f) for f in findings if f["level"] == "citations"],
        "quotes": [dict(f) for f in findings if f["level"] == "quotes"],
        "claims": [dict(f) for f in findings if f["level"] == "claims"],
    }
    for f in by_level["quotes"]:
        if f["type"] == "quote_modified":
            f["diff_html"] = word_diff_html(f["quote"], f["closest_match"])
    # uncited_reference findings can number in the dozens on early drafts;
    # aggregate them into one collapsible card instead of one row each.
    uncited = [f for f in by_level["citations"] if f["type"] == "uncited_reference"]
    by_level["citations"] = [
        f for f in by_level["citations"] if f["type"] != "uncited_reference"
    ]
    # The inventory lists every citation with its status, so orphan rows
    # would be duplicates there; the section chip still counts them.
    citations_chip = by_level["citations"] + uncited
    inventory = report.get("citations", [])
    if inventory:
        by_level["citations"] = [
            f for f in by_level["citations"] if f["type"] != "orphan_citation"
        ]
    claims_by_source: dict[str, list[dict]] = {}
    for f in by_level["claims"]:
        claims_by_source.setdefault(f.get("source_id", "?"), []).append(f)
    sources_by_id = {s["id"]: s for s in report["sources"]}

    def _file_uri(raw: str | None) -> str | None:
        # file:// links only work on the machine that ran the audit; a
        # shared report renders fine, its local links are simply inert.
        if raw is None:
            return None
        try:
            path = Path(raw).resolve()
            return path.as_uri() if path.exists() else None
        except (OSError, ValueError):
            return None

    pdf_links = {
        sid: _file_uri(s.get("pdf")) for sid, s in sources_by_id.items()
    }
    manuscript_link = _file_uri(report["manuscript"].get("path"))

    template = _env.get_template("report.html.j2")
    rendered = template.render(
        report=report, by_level=by_level, uncited=uncited,
        inventory=inventory, citations_chip=citations_chip,
        pdf_links=pdf_links, manuscript_link=manuscript_link,
        claims_by_source=claims_by_source, sources_by_id=sources_by_id,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "report.html"
    path.write_text(rendered, encoding="utf-8")
    return path
