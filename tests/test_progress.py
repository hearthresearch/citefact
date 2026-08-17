"""Tests for structured progress events and their renderers."""

from __future__ import annotations

import io

from citefact.progress import ProgressEvent, null_renderer, plain_renderer, rich_renderer


class TestPlainRenderer:
    def test_message_only_event(self):
        buf = io.StringIO()
        render = plain_renderer(buf)
        render(ProgressEvent(phase="load", message="Loaded 89 entries."))
        assert buf.getvalue() == "Loaded 89 entries.\n"

    def test_counted_event_gets_prefix(self):
        buf = io.StringIO()
        render = plain_renderer(buf)
        render(ProgressEvent(phase="convert", message="Converting a.pdf...",
                             current=3, total=86))
        assert buf.getvalue() == "[3/86] Converting a.pdf...\n"

    def test_cost_is_shown_when_present(self):
        buf = io.StringIO()
        render = plain_renderer(buf)
        render(ProgressEvent(phase="verify", message="smith2023",
                             current=2, total=41, cost_usd=0.13))
        out = buf.getvalue()
        assert "[2/41]" in out and "$0.13" in out


class TestNullRenderer:
    def test_swallows_everything(self):
        null_renderer(ProgressEvent(phase="load", message="x"))  # no error, no output


class TestRichRenderer:
    def test_creates_one_bar_per_counted_phase_and_advances(self):
        from rich.console import Console

        console = Console(file=io.StringIO(), force_terminal=True, width=100)
        with rich_renderer(console=console) as render:
            render(ProgressEvent(phase="convert", message="a.pdf", current=1, total=3))
            render(ProgressEvent(phase="convert", message="b.pdf", current=2, total=3))
            render(ProgressEvent(phase="verify", message="s1", current=1, total=5))
            bars = render.progress.tasks
            assert len(bars) == 2
            convert_bar = bars[0]
            assert convert_bar.total == 3
            assert convert_bar.completed == 2

    def test_message_only_events_print_a_line(self):
        from rich.console import Console

        out = io.StringIO()
        console = Console(file=out, force_terminal=True, width=100)
        with rich_renderer(console=console) as render:
            render(ProgressEvent(phase="load", message="Loaded 89 entries."))
        assert "Loaded 89 entries." in out.getvalue()


class TestCheckmarks:
    def test_finished_bar_shows_check_instead_of_spinner(self):
        from rich.console import Console
        from rich.progress import SpinnerColumn

        console = Console(file=io.StringIO(), force_terminal=True, width=100)
        with rich_renderer(console=console) as render:
            render(ProgressEvent(phase="convert", message="a.pdf", current=1, total=2))
            render(ProgressEvent(phase="convert", message="b.pdf", current=2, total=2))
            task = render.progress.tasks[0]
            assert task.finished
            spinner = render.progress.columns[0]
            assert isinstance(spinner, SpinnerColumn)
            assert "✔" in str(spinner.finished_text)

    def test_long_detail_names_are_truncated(self):
        from rich.console import Console

        console = Console(file=io.StringIO(), force_terminal=True, width=100)
        long_name = "Wolfswinkel et al. - 2013 - Using grounded theory as a method for rigorously reviewing literature.pdf"
        with rich_renderer(console=console) as render:
            render(ProgressEvent(phase="convert", message=long_name,
                                 current=1, total=2))
            detail = render.progress.tasks[0].fields["detail"]
            assert len(detail) <= 44
            assert detail.endswith("…")
