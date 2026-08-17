"""Environment diagnostics: what is installed, what is missing, what to do.

Every check is read-only and degrades gracefully; `doctor` never fails the
process. Statuses: "ok" (works now), "warn" (an optional capability is
unavailable), "missing" (a required-for-some-feature tool is absent).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass

_DOCLING_PROBE_TIMEOUT = 20  # seconds; a warmed env answers in ~2 s
_ZOTERO_URL = "http://localhost:23119/api/users/0/collections?limit=1"


@dataclass
class Check:
    name: str
    status: str  # "ok" | "warn" | "missing"
    detail: str
    hint: str = ""


def _http_ok(url: str, timeout: float = 2.0) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            return response.status == 200
    except Exception:
        return False


def check_python() -> Check:
    version = ".".join(str(n) for n in sys.version_info[:3])
    return Check(name="python", status="ok", detail=f"Python {version}")


def check_uv() -> Check:
    path = shutil.which("uvx") or shutil.which("uv")
    if path is not None:
        return Check(name="uv", status="ok", detail=path)
    return Check(
        name="uv", status="missing",
        detail="uv/uvx not on PATH",
        hint="PDF conversion needs uv: https://docs.astral.sh/uv/ "
             "(curl -LsSf https://astral.sh/uv/install.sh | sh)",
    )


def check_docling() -> Check:
    if shutil.which("uvx") is None and shutil.which("uv") is None:
        return Check(
            name="docling", status="missing",
            detail="cannot run without uv",
            hint="install uv first; docling is fetched automatically",
        )
    try:
        result = subprocess.run(
            ["uvx", "--from", "docling>=2.67.0", "docling", "--version"],
            capture_output=True, text=True, timeout=_DOCLING_PROBE_TIMEOUT,
        )
    except (subprocess.TimeoutExpired, OSError):
        return Check(
            name="docling", status="warn",
            detail="not warmed (first conversion downloads ~1-2 GB)",
            hint="run `citefact setup` to pre-download it",
        )
    if result.returncode == 0:
        version = result.stdout.strip().split("\n")[0]
        return Check(name="docling", status="ok", detail=version)
    return Check(
        name="docling", status="warn",
        detail=f"probe failed (exit {result.returncode})",
        hint="run `citefact setup` to pre-download it",
    )


def check_llm_env() -> Check:
    from citefact.config import config_path, load_config

    known = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "CITEFACT_MODEL")
    present = [name for name in known if os.environ.get(name)]
    if present:
        return Check(name="llm", status="ok",
                     detail=f"{', '.join(present)} set (environment)")
    llm = load_config().get("llm", {})
    if llm.get("api_key"):
        provider = llm.get("provider", "?")
        return Check(name="llm", status="ok",
                     detail=f"{provider} key set (config file: {config_path()})")
    return Check(
        name="llm", status="warn",
        detail="no API key configured",
        hint="run `citefact setup` to configure claim verification; "
             "the deterministic checks run keyless with --skip-claims",
    )


def check_zotero() -> Check:
    if _http_ok(_ZOTERO_URL):
        return Check(name="zotero", status="ok",
                     detail="local API reachable on localhost:23119")
    return Check(
        name="zotero", status="warn",
        detail="local API not reachable (Zotero closed or API disabled)",
        hint="only needed for --zotero-collection; start Zotero 7+ to use it",
    )


def run_doctor() -> list[Check]:
    return [check_python(), check_uv(), check_docling(), check_llm_env(), check_zotero()]
