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

app = typer.Typer(
    no_args_is_help=True,
    add_completion=False,
    rich_markup_mode="markdown",
    context_settings={"help_option_names": ["-h", "--help"]},
)


def _version_callback(value: bool) -> None:
    if value:
        typer.echo(f"citefact {citefact.__version__}")
        raise typer.Exit()


@app.callback()
def main(
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Print the version and exit.",
    ),
) -> None:
    """Audit a manuscript against the full text of its cited sources.

    Three checks, cheapest first: citations (every in-text citation resolves
    to a bibliography entry, deterministic), quotes (every quoted passage is
    verbatim in its source, deterministic), claims (each cited claim is
    supported by the source, LLM with your own key).

    To audit: `citefact doctor` (what is installed), then
    `citefact check MANUSCRIPT --zotero-collection NAME` or
    `citefact check MANUSCRIPT --bib refs.bib --pdfs DIR`. Add
    `--skip-claims` to stay keyless and offline, `--json` to get
    `report.json` on stdout. The report lands in `--out` (default
    ./citefact-report/): report.html for people, report.json for programs.

    Exit codes, every command: 0 clean, 1 findings at or above --fail-on
    (check only), 2 usage or execution error (reason on stderr).
    Progress and summary go to stderr; stdout carries only what --json asks
    for, so it is safe to pipe.

    Environment: CITEFACT_CACHE relocates the .citefact/ cache (default:
    next to the manuscript); CITEFACT_MODEL pins the LLM;
    CITEFACT_MAX_SOURCE_CHARS sets how much of one source reaches the model
    in the claims check (default 300000, about a long article; anything cut
    is reported); PAGEBOUND_CACHE points at a shared cache of converted
    PDFs, read but never written.
    """


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
    manuscript: Path = typer.Argument(
        ..., exists=True, readable=True,
        help="Manuscript to audit (Markdown). Citations are author-year in "
             "the text, e.g. (Smith, 2023) or Smith (2023).",
    ),
    bib: Optional[Path] = typer.Option(
        None, "--bib", exists=True,
        help="Bibliography as BibTeX. Pair with --pdfs. One of --bib or "
             "--zotero-collection is required, not both.",
    ),
    zotero_collection: Optional[str] = typer.Option(
        None, "--zotero-collection",
        help='Bibliography from a Zotero collection, by name or nested path '
             '("PhD/Chapter 3"); reads metadata and attached PDFs through the '
             "local API, so Zotero 7+ must be running.",
    ),
    pdfs: Optional[Path] = typer.Option(
        None, "--pdfs", exists=True, file_okay=False,
        help="Directory of source PDFs, matched to entries by filename stem "
             "(= BibTeX key) or by title. Entries without a PDF become "
             "missing_source findings; quotes and claims against them are "
             "skipped, never the run.",
    ),
    out: Path = typer.Option(
        Path("./citefact-report"), "--out",
        help="Directory for report.html and report.json (created if needed).",
    ),
    skip_claims: bool = typer.Option(
        False, "--skip-claims",
        help="Run only the deterministic checks: no LLM, no key, no network.",
    ),
    only: Optional[str] = typer.Option(
        None, "--only",
        help="Comma-separated subset of levels to run: citations,quotes,claims.",
    ),
    model: Optional[str] = typer.Option(
        None, "--model",
        help="LLM for the claims check, as a LiteLLM id "
             "(anthropic/claude-sonnet-5, openai/gpt-5, ollama/llama3.1). "
             "Beats CITEFACT_MODEL and the setup config.",
    ),
    provider: Optional[str] = typer.Option(
        None, "--provider",
        help="Pick that provider's default model: anthropic, openai or ollama.",
    ),
    fail_on: str = typer.Option(
        "error", "--fail-on",
        help="Severity that makes the exit code 1: error, warning, or none "
             "(always exit 0 unless the run itself fails).",
    ),
    json_out: bool = typer.Option(
        False, "--json",
        help="Also print report.json to stdout (the file is written regardless).",
    ),
    quiet: bool = typer.Option(
        False, "--quiet", help="No progress, no summary on stderr.",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Ignore every cache: reconvert PDFs (retrying ones that were "
             "given up on) and re-verify every claim.",
    ),
) -> None:
    """Audit MANUSCRIPT: citations exist, quotes are verbatim, claims are supported.

    Writes report.html and report.json to --out, prints a one-line summary
    to stderr, exits 0 or 1 by --fail-on, 2 on an execution error. Source
    PDFs are converted once and cached (.citefact/ next to the manuscript);
    an unchanged re-run is seconds and costs nothing.

    report.json (schema_version 3): `summary` {errors, warnings,
    claims_total, verdicts, sources_truncated: source ids whose text was cut
    to CITEFACT_MAX_SOURCE_CHARS before the model read it}; `sources[]` {id, title, converted, conversion:
    {converter_version, text_layer}}, where text_layer false means OCR text;
    `findings[]` each with `level`, `type`, `severity` and a `location`
    line. Types by level: citations: orphan_citation (error: cited, not in
    the bibliography), uncited_reference (warning; info with Zotero),
    missing_source (warning: no PDF or unconvertible); quotes (each with
    `form` inline for text between quotation marks or block for a Markdown
    `>` block quote, APA's form for 40+ words): quote_modified (error, with
    closest_match and similarity 0-100), quote_not_found (error),
    quote_unattributed (warning), quote_page_mismatch (warning: found
    verbatim, but not on the page the citation names), quote_verified
    (info). A matched quote carries `pdf_page` (1-based page in the PDF),
    `page_found` (the printed page number, null when the PDF's numbering
    could not be established) and, when the citation had a `p.`/`pp.`
    locator, `page_claimed` (list of pages); claims: claim_verdict with `verdict` supported (info), partial
    (warning), misrepresented / not_in_paper (error), unverified (warning:
    LLM failure, run marked partial), each with the verbatim `claim`, the
    source `evidence`, `reasoning` and `confidence` 0-100, plus
    `source_truncated` true with `source_chars` and `source_chars_used` when
    the verdict was formed on a cut source (a not_in_paper there may mean
    "not in the part the model saw").
    """
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
    """Report what is installed and what is missing for each feature.

    One line per check (python, uv, docling, llm, zotero) with a status:
    [ok], [warn] (an optional feature is unavailable) or [missing] (a tool
    some feature needs is absent), followed by a hint. Read-only, no
    network beyond a probe of the local Zotero API, always exits 0. Run it
    before the first `check`, or when a check reports missing_source for
    every entry.
    """
    from citefact.doctor import run_doctor

    _print_checks(run_doctor())


def _warm_docling() -> None:
    """Pre-download the Docling environment (one-time, ~1-2 GB)."""
    import subprocess

    from pagebound.convert import DOCLING_SPEC

    typer.echo("Warming the Docling environment (one-time download, ~1-2 GB)...", err=True)
    subprocess.run(
        ["uv", "tool", "run", "--from", DOCLING_SPEC, "docling", "--version"],
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
    provider: str = typer.Option(
        "anthropic", "--provider",
        help="Provider named in the non-interactive hint: anthropic, openai "
             "or ollama.",
    ),
) -> None:
    """Check the environment and prepare it: warm Docling, configure the LLM key.

    Runs the doctor checks, then pre-downloads the Docling environment if
    it is not there yet (one-time, ~1-2 GB into uv's cache) and, on a
    terminal, asks for an LLM API key, validates it with a minimal call
    and saves it to ~/.config/citefact/config.toml (owner-only). Without a
    terminal it prints the `export` lines to add instead and writes
    nothing. The config file is the only thing written outside the
    project. Exits 0 in every case; keyless use needs no setup.
    """
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
    pdfs: Path = typer.Option(
        ..., "--pdfs", exists=True, file_okay=False,
        help="Directory of PDFs to convert (*.pdf, not recursive).",
    ),
    force: bool = typer.Option(
        False, "--force",
        help="Reconvert PDFs already in the cache, including ones given up on.",
    ),
) -> None:
    """Pre-convert PDFs to populate the cache; optional, `check` converts on demand.

    Useful to pay the conversion time (15-30 s per paper, more on the
    first run while Docling downloads) ahead of an audit, or in CI. Writes
    under $CITEFACT_CACHE, default ./.citefact relative to the current
    directory, so run it from the manuscript's folder. Progress goes to
    stderr, one line per PDF. Exits 0 when at least one PDF converted, 2
    when every PDF failed (each failure is logged with its reason).
    """
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


@app.command("help")
def help_command(
    ctx: typer.Context,
    command: Optional[str] = typer.Argument(
        None, metavar="[COMMAND]", help="A command name; omit for the overview.",
    ),
) -> None:
    """Show help for a command, the same as `citefact COMMAND --help`."""
    root = ctx.parent or ctx
    group = root.command
    if command is None:
        typer.echo(group.get_help(root))
        return
    target = group.get_command(root, command)  # type: ignore[attr-defined]
    if target is None:
        typer.echo(f"no such command: {command}", err=True)
        raise typer.Exit(code=2)
    with typer.Context(target, info_name=command, parent=root) as sub:
        typer.echo(target.get_help(sub))
