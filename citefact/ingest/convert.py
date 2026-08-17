"""PDF-to-markdown conversion via Docling, run through uvx.

Docling stays out of citefact's dependency tree (torch alone is ~900 MB);
`uvx` resolves and caches it on first use. Known failure mode: crash
exits (signal kills: returncode < 0, or 128+signal when uvx surfaces a
signal-killed tool, e.g. SIGSEGV -> 139) get ONE retry with --no-ocr,
because the RapidOCR engine segfaults on specific embedded images while
text-layer PDFs convert fine without OCR. Ordinary nonzero exits (corrupt
PDF, bad args) are NOT retried: that would double conversion time for
nothing.
"""

from __future__ import annotations

import hashlib
import logging
import subprocess
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

_TIMEOUT = 600  # first run downloads the docling env (~1-2 GB) into uv's cache


def pdf_sha256(pdf_path: Path) -> str:
    return hashlib.sha256(pdf_path.read_bytes()).hexdigest()


def _is_crash_exit(returncode: int) -> bool:
    return returncode < 0 or returncode >= 128


def _run_docling(pdf_path: Path, out_dir: Path, *, ocr: bool) -> subprocess.CompletedProcess:
    cmd = [
        "uvx", "--from", "docling>=2.67.0", "docling",
        str(pdf_path), "--to", "md", "--output", str(out_dir),
    ]
    if not ocr:
        cmd.append("--no-ocr")
    return subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)


def convert_pdf(pdf_path: Path, cache_dir: Path, *, force: bool = False) -> str | None:
    """Return the PDF's markdown text, converting and caching as needed.

    Returns None when conversion fails; callers degrade the source to
    `missing_source` and continue (never fail the whole audit for one PDF).
    """
    try:
        sources_dir = cache_dir / "cache" / "sources"
        sources_dir.mkdir(parents=True, exist_ok=True)
        cached = sources_dir / f"{pdf_sha256(pdf_path)}.md"
        if cached.exists() and not force:
            return cached.read_text(encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            result = _run_docling(pdf_path, out_dir, ocr=True)
            if result.returncode != 0 and _is_crash_exit(result.returncode):
                log.warning(
                    "docling crashed (exit %s) on %s; retrying with --no-ocr",
                    result.returncode, pdf_path.name,
                )
                result = _run_docling(pdf_path, out_dir, ocr=False)
            if result.returncode != 0:
                log.warning("docling failed on %s: %s", pdf_path.name, result.stderr[-500:])
                return None
            produced = out_dir / f"{pdf_path.stem}.md"
            if not produced.exists():
                log.warning("docling produced no markdown for %s", pdf_path.name)
                return None
            text = produced.read_text(encoding="utf-8")

        cached.write_text(text, encoding="utf-8")
        return text
    except (subprocess.TimeoutExpired, OSError, UnicodeDecodeError) as exc:
        log.warning("Could not convert %s: %s", pdf_path.name, exc)
        return None
