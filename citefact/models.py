"""Typed core data model shared by ingest, checks, and report."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

SEVERITY_ORDER = {"error": 2, "warning": 1, "info": 0}


@dataclass
class Finding:
    """One audit finding. `details` holds level-specific payload that is
    merged flat into the report JSON (the report.json findings shape)."""

    level: str  # "citations" | "quotes" | "claims"
    type: str
    severity: str  # "error" | "warning" | "info"
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "level": self.level,
            "type": self.type,
            "severity": self.severity,
            **self.details,
        }


@dataclass
class Manuscript:
    path: Path
    text: str
    sha256: str
    words: int


@dataclass
class Source:
    """A bibliography entry plus everything learned about its PDF."""

    id: str
    title: str
    authors: str
    year: int | str | None
    pdf_path: Path | None = None
    content_hash: str | None = None  # sha256 of the PDF bytes
    text: str | None = None  # converted markdown; None = unavailable

    @property
    def converted(self) -> bool:
        return self.text is not None


def line_of(text: str, offset: int) -> int:
    """1-based line number of a character offset."""
    return text.count("\n", 0, offset) + 1
