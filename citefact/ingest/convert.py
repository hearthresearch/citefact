"""PDF-to-markdown conversion through pagebound.

pagebound owns the Docling invocation (via `uv tool run`, so torch never
enters citefact's dependency tree), the one-shot `--no-ocr` retry on a
native crash, the tombstones for PDFs that keep failing, and a store
keyed on the PDF's sha256 plus the conversion recipe, so a Docling
upgrade produces a new artifact instead of silently redefining an old one.

citefact adds the cache policy:

- the writable store lives under the project cache
  (`.citefact/pagebound`), the only place citefact ever writes;
- the user's shared pagebound cache (`~/.cache/pagebound`, or
  `PAGEBOUND_CACHE`) is consulted as a read-only fallback, so a paper
  another tool already converted is copied in rather than reconverted;
  an absent shared cache is never created;
- scans are OCR'd rather than refused, and the artifact records that,
  so the report can mark OCR-derived text as weaker evidence;
- every failure degrades to None: the source becomes `missing_source`
  and the audit continues.

The markdown pagebound hands back is rendered from Docling's JSON, so it
carries no serializer escapes (`&amp;`, `\\_`); the quote check relies on
that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import pagebound
from pagebound import PageboundError, Store
from pagebound.recipe import Recipe
from pagebound.store import default_root, sha256_of

log = logging.getLogger(__name__)

PROJECT_STORE_DIRNAME = "pagebound"

# Injection points for tests; None means pagebound's defaults (a recipe
# resolved from the Docling that would run, the real converter and prober).
_RECIPE: Recipe | None = None
_CONVERTER: Callable[..., Path] | None = None
_PROBER: Callable[..., dict[str, Any]] | None = None


@dataclass(frozen=True)
class Converted:
    """A source's text plus the provenance of the conversion that produced it."""

    text: str
    sha256: str
    converter: str
    converter_version: str
    converted_at: str
    text_layer: bool | None  # False: OCR-derived text; None: not recorded

    def provenance(self) -> dict[str, Any]:
        return {
            "converter": self.converter,
            "converter_version": self.converter_version,
            "converted_at": self.converted_at,
            "text_layer": self.text_layer,
        }


def pdf_sha256(pdf_path: Path) -> str:
    return sha256_of(pdf_path)


def project_store(cache_dir: Path) -> Store:
    return Store(cache_dir / PROJECT_STORE_DIRNAME)


def shared_stores() -> list[Store]:
    """The user's shared pagebound cache, read-only, only if it exists."""
    root = default_root()
    return [Store(root)] if root.is_dir() else []


def _converted(artifact: pagebound.Artifact) -> Converted:
    document = artifact.document
    return Converted(
        text=artifact.markdown,
        sha256=document.source.sha256,
        converter=document.conversion.converter,
        converter_version=document.conversion.converter_version,
        converted_at=document.conversion.converted_at,
        text_layer=document.source.text_layer,
    )


def convert_pdf(pdf_path: Path, cache_dir: Path, *, force: bool = False) -> Converted | None:
    """Return the PDF's text and conversion provenance, converting as needed.

    Returns None when conversion fails; callers degrade the source to
    `missing_source` and continue (never fail the whole audit for one PDF).
    """
    store = project_store(cache_dir)
    fallbacks = shared_stores()
    try:
        artifact = None
        if not force:
            # Any recipe will do for a read, and this asks Docling nothing,
            # so a fully cached run never pays for `docling --version`.
            artifact = pagebound.get_cached_document(
                pdf_path, store=store, fallback_stores=fallbacks,
            )
        if artifact is None:
            artifact = pagebound.get_document(
                pdf_path, store=store, fallback_stores=fallbacks,
                recipe=_RECIPE, converter=_CONVERTER, prober=_PROBER,
                ocr_scans=True, force=force,
            )
    except PageboundError as exc:
        log.warning("Could not convert %s: %s", pdf_path.name, exc)
        return None
    return _converted(artifact)
