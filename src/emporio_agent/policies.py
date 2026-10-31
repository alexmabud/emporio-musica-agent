"""Turn the store policy manual (PDF) into clean text for the system prompt.

The manual is short (8 pages), so it is placed whole in the prompt instead of being retrieved by
similarity search. This module only extracts and tidies the text; it never rewrites its meaning.
"""

import re
from pathlib import Path

from pypdf import PdfReader

_FOOTER = re.compile(r"Página \d+")
_RUNNING_HEADER = "Empório da Música Manual de Políticas e Procedimentos"
_COLUMN_GAP = re.compile(r" {3,}")
_BULLET = re.compile(r"^•\s+")
_NUMBERED = re.compile(r"^(\d+)\.\s+")

# A line indented this much deeper than the line right above it is that line's wrapped tail.
_CONTINUATION_INDENT = 3

# Typos in the manual itself, listed in docs/assumptions.md.
_TEXT_FIXES = {"contato@empóriodamusica.com.br": "contato@emporiodamusica.com.br"}


def _indent(raw: str) -> int:
    """Number of leading spaces of a layout line."""
    return len(raw) - len(raw.lstrip())


def load_policy_text(pdf_path: Path) -> str:
    """Extract the policy manual as readable text.

    Page footers and the header repeated on every page are dropped, table columns are separated
    by `` | ``, bullets become ``- `` lines and numbered steps stay on their own line. Lines that
    wrapped inside a table cell or a bullet are joined back, runs of blank lines are collapsed and
    known typos are fixed.

    Args:
        pdf_path: Path of the manual PDF.

    Returns:
        The cleaned manual text.
    """
    lines: list[str] = []
    previous_indent = 0
    for page in PdfReader(pdf_path).pages:
        for raw in page.extract_text(extraction_mode="layout").splitlines():
            line = _NUMBERED.sub(r"\1. ", _BULLET.sub("- ", raw.strip()))
            line = _COLUMN_GAP.sub(" | ", line)
            if _FOOTER.fullmatch(line) or line == _RUNNING_HEADER:
                continue
            if not line:
                if lines and lines[-1]:
                    lines.append("")
                continue
            wrapped = _indent(raw) >= previous_indent + _CONTINUATION_INDENT
            starts_item = line.startswith("- ") or bool(_NUMBERED.match(line))
            if lines and lines[-1] and wrapped and not starts_item:
                lines[-1] = f"{lines[-1]} {line}"
            else:
                lines.append(line)
            previous_indent = _indent(raw)
    text = "\n".join(lines).strip()
    for wrong, right in _TEXT_FIXES.items():
        text = text.replace(wrong, right)
    return text
