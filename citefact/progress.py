"""Structured progress events and their terminal renderers.

The pipeline and checks emit `ProgressEvent`s through a plain callable;
they never know how progress is displayed. The CLI picks a renderer:

- `rich_renderer`: live bars with ETA on interactive terminals
- `plain_renderer`: one line per event (CI, pipes, non-TTY)
- `null_renderer`: `--quiet`
"""

from __future__ import annotations

from contextlib import AbstractContextManager
from dataclasses import dataclass
from typing import Callable, Optional, TextIO

from rich.console import Console
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)


@dataclass
class ProgressEvent:
    """One unit of progress.

    `current`/`total` present: a step in a counted phase (drawn as a bar).
    Absent: a free-form status line. `cost_usd` is the RUNNING total for
    LLM phases, shown live so cost is never a surprise at the end.
    """

    phase: str  # "load" | "match" | "convert" | "extract" | "verify" | "report"
    message: str
    current: Optional[int] = None
    total: Optional[int] = None
    cost_usd: Optional[float] = None


ProgressFn = Callable[[ProgressEvent], None]


def null_renderer(event: ProgressEvent) -> None:
    """Swallow everything (--quiet)."""


def plain_renderer(stream: TextIO) -> ProgressFn:
    """Line-per-event renderer for non-interactive terminals."""

    def render(event: ProgressEvent) -> None:
        parts: list[str] = []
        if event.current is not None and event.total is not None:
            parts.append(f"[{event.current}/{event.total}]")
        parts.append(event.message)
        if event.cost_usd is not None:
            parts.append(f"(${event.cost_usd:.2f})")
        print(" ".join(parts), file=stream, flush=True)

    return render


_PHASE_LABELS = {
    "convert": "Converting PDFs",
    "verify": "Verifying claims",
}
_DETAIL_MAX = 40  # long Zotero filenames must not wrap the bar line


def _truncate(text: str, limit: int = _DETAIL_MAX) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


class _RichRenderer(AbstractContextManager):
    """Callable context manager drawing one live bar per counted phase."""

    def __init__(self, console: Optional[Console] = None):
        self.progress = Progress(
            SpinnerColumn(finished_text="[green]✔[/green]"),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            MofNCompleteColumn(),
            TimeElapsedColumn(),
            TimeRemainingColumn(),
            TextColumn("{task.fields[detail]}"),
            console=console,
            transient=False,
        )
        self._task_ids: dict[str, int] = {}

    def __enter__(self) -> "_RichRenderer":
        self.progress.__enter__()
        return self

    def __exit__(self, *exc) -> None:
        self.progress.__exit__(*exc)

    def __call__(self, event: ProgressEvent) -> None:
        if event.current is None or event.total is None:
            self.progress.console.print(event.message, highlight=False)
            return
        detail = _truncate(event.message)
        if event.cost_usd is not None:
            detail = f"${event.cost_usd:.2f}  {detail}"
        if event.phase not in self._task_ids:
            self._task_ids[event.phase] = self.progress.add_task(
                _PHASE_LABELS.get(event.phase, event.phase.capitalize()),
                total=event.total,
                detail=detail,
            )
        self.progress.update(
            self._task_ids[event.phase],
            completed=event.current,
            total=event.total,
            detail=detail,
        )


def rich_renderer(console: Optional[Console] = None) -> _RichRenderer:
    return _RichRenderer(console=console)
