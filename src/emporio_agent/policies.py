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
_TABLE_SEPARATOR = " | "

# Cover lines that carry no policy (they are page furniture, like the footer).
_NOISE_LINES = frozenset({"Documento interno — Uso restrito da equipe de atendimento"})

# Titles of the ten sections ("7. Atendimento via WhatsApp"), not the steps ("1. Saudação: ...").
_SECTION_TITLE = re.compile(r"^\d{1,2}\. [^:.]{3,60}$")
# Any heading or list item: it starts a line of its own and never continues the previous one.
_STARTS_BLOCK = re.compile(r"^(\d+(\.\d+)*\.? |- )")
_ENDS_SENTENCE = re.compile(r"[.!?:;]$")
_BULLET = re.compile(r"^•\s+")
_NUMBERED = re.compile(r"^(\d+)\.\s+")

# A line indented this much deeper than the line right above it is that line's wrapped tail.
_CONTINUATION_INDENT = 3

# Typos in the manual itself, listed in docs/assumptions.md.
_TEXT_FIXES = {"contato@empóriodamusica.com.br": "contato@emporiodamusica.com.br"}


def _indent(raw: str) -> int:
    """Number of leading spaces of a layout line."""
    return len(raw) - len(raw.lstrip())


def _reflow(lines: list[str]) -> list[str]:
    """Join the lines that the PDF layout wrapped in the middle of a sentence.

    A line is appended to the one above only when the one above does not end a sentence, is not a
    heading or a table row, and the line itself does not start a heading, bullet or step.
    """
    joined: list[str] = []
    for line in lines:
        previous = joined[-1] if joined else ""
        continues = (
            bool(line)
            and bool(previous)
            and _TABLE_SEPARATOR not in line
            and _TABLE_SEPARATOR not in previous
            and not _STARTS_BLOCK.match(line)
            and not _STARTS_BLOCK.match(previous)
            and not _ENDS_SENTENCE.search(previous)
        )
        if continues:
            joined[-1] = f"{previous} {line}"
        else:
            joined.append(line)
    return joined


def _flatten_table(rows: list[list[str]]) -> list[str]:
    """Turn the rows of one table into simple lines, dropping the header row.

    Two-column tables become ``first: second``. Wider tables keep the column names, so no value
    loses its meaning: ``PAC (Correios): Prazo Estimado 5 a 12 dias úteis; Rastreamento Sim``.
    """
    header, *body = rows
    if len(header) == 2:
        return [f"{row[0]}: {row[1]}" for row in body]
    return [
        f"{row[0]}: "
        + "; ".join(f"{name} {value}" for name, value in zip(header[1:], row[1:], strict=False))
        for row in body
    ]


def _blank_before_table_row(lines: list[str], index: int) -> bool:
    """Tell whether ``lines[index]`` is a blank line sitting between two rows of one table."""
    return not lines[index] and index + 1 < len(lines) and _TABLE_SEPARATOR in lines[index + 1]


def _flatten_tables(lines: list[str]) -> list[str]:
    """Replace every run of table rows (blank lines in between included) by simple lines."""
    result: list[str] = []
    index = 0
    while index < len(lines):
        if _TABLE_SEPARATOR not in lines[index]:
            result.append(lines[index])
            index += 1
            continue
        rows: list[list[str]] = []
        while index < len(lines) and (
            _TABLE_SEPARATOR in lines[index] or _blank_before_table_row(lines, index)
        ):
            if lines[index]:
                rows.append(lines[index].split(_TABLE_SEPARATOR))
            index += 1
        result.extend(_flatten_table(rows))
    return result


def _space_sections(lines: list[str]) -> list[str]:
    """Drop every blank line, then put one back before each numbered section title."""
    spaced: list[str] = []
    for line in (line for line in lines if line):
        if _SECTION_TITLE.match(line) and spaced:
            spaced.append("")
        spaced.append(line)
    return spaced


def load_policy_text(pdf_path: Path) -> str:
    """Extract the policy manual as readable text.

    Page footers and the header repeated on every page are dropped, table columns are separated
    by `` | `` and then turned into simple ``name: value`` lines, bullets become ``- `` lines and
    numbered steps stay on their own line. Lines that wrapped inside a sentence are joined back,
    blank lines are kept only before each section title, the "internal use" cover line is dropped
    and known typos are fixed. Nothing that states a rule, a value or a deadline is removed
    (the tests check it).

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
    lines = [line for line in lines if line not in _NOISE_LINES]
    text = "\n".join(_space_sections(_flatten_tables(_reflow(lines)))).strip()
    for wrong, right in _TEXT_FIXES.items():
        text = text.replace(wrong, right)
    return text
