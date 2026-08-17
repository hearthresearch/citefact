"""Prompt template loader for LLM prompts.

System prompts are stored as .txt files (plain text, no variables).
User prompts are stored as .j2 files (Jinja2 templates with {{ var }} syntax).
"""

from functools import lru_cache
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, StrictUndefined

PROMPTS_DIR = Path(__file__).parent / "prompts"

_jinja_env = Environment(
    loader=FileSystemLoader(PROMPTS_DIR),
    undefined=StrictUndefined,
    trim_blocks=True,
    lstrip_blocks=True,
    keep_trailing_newline=False,
)


@lru_cache(maxsize=None)
def load_system_prompt(name: str) -> str:
    """Load a system prompt from prompts/{name}.txt.

    Results are cached permanently since system prompts never change at runtime.
    """
    path = PROMPTS_DIR / f"{name}.txt"
    return path.read_text(encoding="utf-8").strip()


def render_user_prompt(name: str, **context: object) -> str:
    """Render a user prompt template from prompts/{name}.j2 with the given context."""
    template = _jinja_env.get_template(f"{name}.j2")
    return template.render(**context)
