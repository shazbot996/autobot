"""An agent's answer in two parts: a spoken summary and a detail visual (AG-4). No drawing.

The summary is what the summary panel shows and autobot reads aloud. The
detail is the companion the agent puts in the detail panel, laid out to fit
the panel as it is, not scrolled into: `layout` folds each line to the
panel's width and keeps the first lines that fit its height.

An agent that calls a language model asks it, in its prompt, for one JSON
object, `{"summary": "...", "detail": ["...", "..."]}`, and
`parse_model_response` takes that apart. It is lenient: a fenced block is
unwrapped, and any text that is not that shape still becomes two parts, its
first sentence as the summary and the whole text as the detail.
"""

from __future__ import annotations

import json
import re
import textwrap
from dataclasses import dataclass

# A fenced block, as a model writes one: ```json ... ```.
_FENCE = re.compile(r"```[A-Za-z0-9_-]*[ \t]*\n?(.*?)```", re.DOTALL)
# The first sentence of a text: up to the first stop followed by a space.
_SENTENCE = re.compile(r"(.+?[.!?])(?=\s|$)")


@dataclass(frozen=True)
class Response:
    """What an agent answers: the spoken summary and the detail visual's lines."""

    summary: str
    detail: tuple[str, ...] = ()


def layout(response: Response, rows: int, cols: int) -> list[str]:
    """The detail fitted to a panel `rows` high and `cols` wide.

    Each line is folded at word breaks to `cols`, a word longer than that is
    cut, and the first `rows` lines are kept.
    """
    if rows <= 0 or cols <= 0:
        return []
    lines: list[str] = []
    for line in response.detail:
        folded = textwrap.wrap(
            line, cols, break_long_words=True, break_on_hyphens=False
        )
        lines.extend(folded or [""])
        if len(lines) >= rows:
            break
    return lines[:rows]


def parse_model_response(text: str) -> Response:
    """The two parts of a model's answer, from the shape the prompt asks for or any text."""
    parsed = _from_json(text)
    if parsed is not None:
        return parsed
    plain = text.strip()
    fenced = _FENCE.search(plain)
    if fenced:
        plain = fenced.group(1).strip()
    return Response(_first_sentence(plain), _lines(plain))


def _from_json(text: str) -> Response | None:
    """The answer as `{"summary": ..., "detail": [...]}`; None when it is not that."""
    candidates = [match.group(1) for match in _FENCE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start : end + 1])
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        summary = data.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            continue
        detail = data.get("detail", [])
        if isinstance(detail, str):
            lines = _lines(detail)
        elif isinstance(detail, list):
            lines = tuple(str(line).strip() for line in detail)
        else:
            lines = ()
        return Response(" ".join(summary.split()), lines)
    return None


def _first_sentence(text: str) -> str:
    flat = " ".join(text.split())
    match = _SENTENCE.match(flat)
    return match.group(1) if match else flat


def _lines(text: str) -> tuple[str, ...]:
    """The text's lines, trimmed, without the blank ones at its ends."""
    return tuple(line.strip() for line in text.strip().splitlines())
