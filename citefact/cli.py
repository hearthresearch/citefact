"""Typer CLI entry point."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Optional

import typer

import citefact
from citefact import pipeline
from citefact.progress import null_renderer, plain_renderer, rich_renderer
from citefact.report.html_out import write_report_html
from citefact.report.json_out import write_report_json

app = typer.Typer(no_args_is_help=True, add_completion=False)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"citefact {citefact.__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True
    ),
) -> None:
    """Audit a manuscript against the full text of its cited sources."""


def _cache_dir(manuscript: Path) -> Path:
    override = os.environ.get("CITEFACT_CACHE")
    return Path(override) if override else manuscript.parent / ".citefact"


def _summary_lines(report: dict, html_path: Path) -> list[str]:
    from collections import Counter

    summary = report["summary"]
    run = report["run"]
    by_level = Counter(
        f["level"] for f in report["findings"] if f["severity"] != "info"
    )
    if summary["errors"] > 0:
        mark = "❌"
    elif summary["warnings"] > 0:
        mark = "⚠️"
    else:
        mark = "✅"
    head = f"{mark} Summary: {summary['errors']} errors, {summary['warnings']} warnings"
    if by_level:
        head += " (" + ", ".join(f"{lvl} {n}" for lvl, n in by_level.items()) + ")"
    if run.get("model") is not None:
        head += f" | cost ${run['cost_usd']:.2f}"
    head += f" | {run['duration_seconds']}s"
    lines = [head]
    if run.get("partial"):
        lines.append("Partial audit: some claims could not be verified.")
    lines.append(f"Report: {html_path}")
    return lines


@app.command()
def check(
    manuscript: Path = typer.Argument(..., exists=True, readable=True),
    bib: Optional[Path] = typer.Option(None, "--bib", exists=True),
    zotero_collection: Optional[str] = typer.Option(
        None, "--zotero-collection",
        help='Zotero collection name or nested path ("PhD/Chapter 3"); '
             "requires Zotero 7+ running (local API)",
    ),
    pdfs: Optional[Path] = typer.Option(None, "--pdfs", exists=True, file_okay=False),
    out: Path = typer.Option(Path("./citefact-report"), "--out"),
    skip_claims: bool = typer.Option(False, "--skip-claims"),
    only: Optional[str] = typer.Option(None, "--only", help="comma-separated levels"),
    model: Optional[str] = typer.Option(None, "--model"),
    provider: Optional[str] = typer.Option(None, "--provider"),
    fail_on: str = typer.Option("error", "--fail-on"),
    json_out: bool = typer.Option(False, "--json"),
    quiet: bool = typer.Option(False, "--quiet"),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Audit MANUSCRIPT: citations exist, quotes are verbatim, claims are supported."""
    from contextlib import nullcontext

    if quiet:
        render_cm = nullcontext(null_renderer)
    elif sys.stderr.isatty():
        render_cm = rich_renderer()  # live bars with ETA
    else:
        render_cm = nullcontext(plain_renderer(sys.stderr))  # CI, pipes

    if fail_on not in ("error", "warning", "none"):
        typer.echo("--fail-on must be error, warning, or none", err=True)
        raise typer.Exit(2)
    if bib is None and zotero_collection is None:
        typer.echo(
            "A bibliography is required: pass --bib refs.bib or "
            "--zotero-collection NAME", err=True,
        )
        raise typer.Exit(2)
    if bib is not None and zotero_collection is not None:
        typer.echo(
            "Pass either --bib or --zotero-collection, not both.", err=True,
        )
        raise typer.Exit(2)

    levels = list(pipeline.ALL_LEVELS)
    if only is not None:
        # dict.fromkeys dedupes while preserving order: "citations,citations"
        # must not run (and duplicate the findings of) a level twice.
        levels = list(dict.fromkeys(l.strip() for l in only.split(",") if l.strip()))
        unknown = set(levels) - set(pipeline.ALL_LEVELS)
        if unknown:
            typer.echo(f"Unknown levels: {', '.join(sorted(unknown))}", err=True)
            raise typer.Exit(2)
    if skip_claims and "claims" in levels:
        levels.remove("claims")
    if not levels:
        typer.echo("No levels selected: nothing to check.", err=True)
        raise typer.Exit(2)

    resolved_model: Optional[str] = None
    if "claims" in levels:
        from citefact.config import apply_config_to_env
        from citefact.llm.client import resolve_model

        apply_config_to_env()  # env still beats config; config beats defaults

        try:
            resolved_model = resolve_model(model, provider)
        except ValueError as exc:
            typer.echo(f"citefact failed: {exc}", err=True)
            raise typer.Exit(2)

    try:
        with render_cm as progress:
            report = pipeline.run_check(
                manuscript, bib_path=bib, zotero_collection=zotero_collection,
                pdf_dir=pdfs, cache_dir=_cache_dir(manuscript), levels=levels,
                model=resolved_model, force=force, progress=progress,
            )
    except Exception as exc:
        typer.echo(f"citefact failed: {exc}", err=True)
        raise typer.Exit(2)

    json_path = write_report_json(report, out)
    html_path = write_report_html(report, out)
    if not quiet:
        for line in _summary_lines(report, html_path):
            typer.echo(line, err=True)
    if json_out:
        typer.echo(json.dumps(report, indent=2, ensure_ascii=False))

    raise typer.Exit(pipeline.exit_code_for(report["findings"], fail_on))


_STATUS_MARK = {"ok": "[ok]     ", "warn": "[warn]   ", "missing": "[missing]"}


def _print_checks(checks) -> None:
    for check in checks:
        typer.echo(f"{_STATUS_MARK[check.status]} {check.name:8} {check.detail}")
        if check.hint and check.status != "ok":
            typer.echo(f"          {'':8} -> {check.hint}")


@app.command()
def doctor() -> None:
    """Report what is installed and what is missing for each feature."""
    from citefact.doctor import run_doctor

    _print_checks(run_doctor())


def _warm_docling() -> None:
    """Pre-download the Docling environment (one-time, ~1-2 GB)."""
    import subprocess

    typer.echo("Warming the Docling environment (one-time download, ~1-2 GB)...", err=True)
    subprocess.run(
        ["uvx", "--from", "docling>=2.67.0", "docling", "--version"],
        timeout=1800,
    )


def _stdin_isatty() -> bool:
    return sys.stdin.isatty()


def _validate_key(provider: str, key: str) -> tuple[bool, str]:
    """Try a minimal LLM call with the key. Returns (ok, model-or-error)."""
    import os

    from citefact.config import key_var_for
    from citefact.llm.client import call_llm, resolve_model

    var = key_var_for(provider)
    previous = os.environ.get(var)
    os.environ[var] = key
    try:
        model = resolve_model(None, provider)
        call_llm([{"role": "user", "content": "ping"}], model=model, max_tokens=1)
        return True, model
    except Exception as exc:
        return False, str(exc)
    finally:
        if previous is None:
            os.environ.pop(var, None)
        else:
            os.environ[var] = previous


_KEY_CONSOLE_URLS = {
    "anthropic": "https://console.anthropic.com/settings/keys",
    "openai": "https://platform.openai.com/api-keys",
}


def _interactive_llm_setup() -> None:
    from citefact.config import load_config, save_config

    if not typer.confirm("Configure claim verification (LLM) now?", default=True):
        return
    provider = typer.prompt("Provider (anthropic / openai / ollama)",
                            default="anthropic")
    if provider == "ollama":
        typer.echo("Ollama runs locally and needs no key; pin a model with "
                   "`export CITEFACT_MODEL=ollama/<model>` or --model.")
        return
    url = _KEY_CONSOLE_URLS.get(provider)
    hint = f" (get one at {url})" if url else ""
    key = typer.prompt(f"Paste your API key{hint}", hide_input=True).strip()
    typer.echo("Validating key with a minimal call...")
    ok, detail = _validate_key(provider, key)
    if ok:
        typer.echo(f"✅ key works ({detail})")
    else:
        typer.echo(f"❌ validation failed: {detail}")
        if not typer.confirm("Save it anyway?", default=False):
            typer.echo("Nothing saved.")
            return
    config = load_config()
    config.setdefault("llm", {})
    config["llm"]["provider"] = provider
    config["llm"]["api_key"] = key
    path = save_config(config)
    typer.echo(f"Saved to {path} (owner-only permissions).")


@app.command()
def setup(
    provider: str = typer.Option("anthropic", "--provider",
                                 help="provider for the non-interactive hint"),
) -> None:
    """Check the environment and prepare it: warm Docling, configure the LLM key."""
    from citefact.config import apply_config_to_env
    from citefact.doctor import run_doctor

    apply_config_to_env()
    checks = run_doctor()
    _print_checks(checks)
    by_name = {c.name: c for c in checks}

    if by_name["docling"].status == "warn" and by_name["uv"].status == "ok":
        _warm_docling()
    elif by_name["uv"].status == "missing":
        typer.echo("Install uv first, then re-run `citefact setup`.", err=True)

    if by_name["llm"].status != "ok":
        if _stdin_isatty():
            _interactive_llm_setup()
        else:
            from citefact.config import key_var_for

            typer.echo("")
            typer.echo("To enable claim verification (LLM), add to your shell profile:")
            typer.echo(f"  export {key_var_for(provider)}=...your key...")
            typer.echo("  # optional, to pin a model: export CITEFACT_MODEL=provider/model")
    typer.echo("")
    typer.echo('Done. Try: citefact check manuscript.md --zotero-collection "My Papers" --skip-claims')


@app.command()
def convert(
    pdfs: Path = typer.Option(..., "--pdfs", exists=True, file_okay=False),
    force: bool = typer.Option(False, "--force"),
) -> None:
    """Pre-convert PDFs to populate the cache."""
    from citefact.ingest.convert import convert_pdf

    cache = Path(os.environ.get("CITEFACT_CACHE", ".citefact"))
    failed = 0
    pdf_list = sorted(pdfs.glob("*.pdf")) + sorted(pdfs.glob("*.PDF"))
    for i, pdf in enumerate(pdf_list, start=1):
        print(f"[{i}/{len(pdf_list)}] Converting {pdf.name}...", file=sys.stderr)
        if convert_pdf(pdf, cache, force=force) is None:
            failed += 1
    print(f"Converted {len(pdf_list) - failed}/{len(pdf_list)} PDFs.", file=sys.stderr)
    raise typer.Exit(2 if failed and failed == len(pdf_list) else 0)
