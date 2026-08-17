"""Shared JSON utilities for LLM response parsing.

LLMs occasionally return JSON that is *almost* valid: trailing commas,
unbalanced braces, control characters, or unescaped `"` inside string
values when the model echoes verbatim quotes. These helpers progressively
repair such output before the caller falls back to a retry.
"""

import json
import re


def repair_json(text: str) -> dict:
    """Progressive JSON repair for LLM output.

    Tries cheaper repairs first, escalates only on failure. Raises
    `json.JSONDecodeError` if every strategy fails.

    Strategies, in order:
      1. Strip trailing commas before `}` / `]`.
      2. Balance unmatched `{`/`}` and `[`/`]`.
      3. Strip control characters (except `\\n`, `\\r`, `\\t`).
      4. Escape `"` that appear *inside* string values (the LLM
         embeds verbatim evidence quotes with literal `"` instead of `\\"`).
    """
    # Stage 1: trailing commas
    text = re.sub(r",\s*([}\]])", r"\1", text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Stage 2: balance braces/brackets
    open_braces = text.count("{") - text.count("}")
    if open_braces > 0:
        text += "}" * open_braces
    open_brackets = text.count("[") - text.count("]")
    if open_brackets > 0:
        text += "]" * open_brackets

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Stage 3: strip control chars
    text = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', text)

    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Stage 4: escape unescaped `"` inside string values
    repaired = _escape_inner_quotes(text)
    return json.loads(repaired)


def _escape_inner_quotes(text: str) -> str:
    """Escape `"` characters that appear inside string values.

    Walks the JSON character-by-character tracking whether we're inside
    a string. Within a string, a `"` followed by something that can't
    plausibly close a JSON value (i.e. not a comma, `}`, `]`, `:`, or
    whitespace + one of those) is treated as an embedded quote and
    rewritten to `\\"`.

    This is a heuristic, not a parser. It handles the common case where
    Claude returns:

        {"narrative": "She said: "hello" to the room."}

    by rewriting the inner quotes. It will not fix every malformed JSON
    string, but combined with `json.loads` retry it converts a hard
    failure into a soft one.
    """
    out: list[str] = []
    i = 0
    n = len(text)
    in_string = False
    escape_next = False

    while i < n:
        ch = text[i]

        if escape_next:
            out.append(ch)
            escape_next = False
            i += 1
            continue

        if ch == "\\":
            out.append(ch)
            escape_next = True
            i += 1
            continue

        if ch == '"':
            if not in_string:
                # opening quote
                out.append(ch)
                in_string = True
                i += 1
                continue

            # We're inside a string and saw `"`. Decide: real close, or embedded?
            j = i + 1
            while j < n and text[j] in " \t\r\n":
                j += 1
            next_meaningful = text[j] if j < n else ""

            if next_meaningful in (",", "}", "]", ":", ""):
                # Real string close.
                out.append(ch)
                in_string = False
            else:
                # Embedded quote — escape it.
                out.append('\\"')
            i += 1
            continue

        out.append(ch)
        i += 1

    return "".join(out)


def strip_fences(text: str) -> str:
    """Strip ```json ... ``` fences if the model wrapped its output."""
    text = text.strip()
    if text.startswith("```"):
        lines = text.split("\n")
        text = "\n".join(lines[1:-1] if lines[-1] == "```" else lines[1:])
    return text


def parse_llm_json_response(
    response_text: str,
    *,
    messages: list[dict],
    model: str,
    cost_sink: list | None = None,
) -> dict:
    """Parse an LLM JSON response with three seatbelts: direct parse,
    progressive repair, one-shot LLM repair retry. Never loop.

    The repair retry makes its own billed LLM call. Pass a list as
    `cost_sink` to have its `cost_usd` appended when the retry fires, so
    callers can fold it into their own cost accounting; omitted (the
    default) skips this entirely, keeping the signature backward
    compatible with callers/stand-ins that don't carry a `cost_usd`.
    """
    text = strip_fences(response_text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    try:
        return repair_json(text)
    except json.JSONDecodeError:
        pass

    from citefact.llm.client import call_llm  # late import: avoid cycle

    repair_messages = list(messages) + [
        {"role": "assistant", "content": response_text},
        {
            "role": "user",
            "content": (
                "Your previous response was not valid JSON. The most likely "
                'cause is a literal `"` character inside one of the string '
                "values (often from a verbatim quote). Return the SAME "
                "content as a single, syntactically valid JSON object: "
                'escape every embedded `"` as `\\"`, or replace it with '
                "the curly variants “ / ”. Output JSON only, "
                "no prose, no markdown fences."
            ),
        },
    ]
    retry = call_llm(repair_messages, model=model, temperature=0.0)
    if cost_sink is not None:
        cost_sink.append(retry.cost_usd)
    return json.loads(strip_fences(retry.text))
