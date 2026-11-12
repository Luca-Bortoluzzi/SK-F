"""Errors, warnings and notes produced while reading a lab file.

The Lexer, the Parser and the translator add their findings to one
``IssueReport``. Every issue can carry the line and column of the element it
refers to, so all problems are reported at once and point into the file.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Union

ERROR = "error"
WARNING = "warning"
NOTE = "note"

PathElement = Union[str, int]
ConfigPath = tuple[PathElement, ...]


@dataclass(frozen=True, order=True)
class Position:
    """1-based line and column in the lab file."""

    line: int
    column: int


@dataclass(frozen=True)
class Issue:
    severity: str
    message: str
    position: Position | None = None
    path: ConfigPath = ()


class DictionaryError(Exception):
    """The dictionary file itself is invalid: a problem of the framework, not of the lab."""


def format_path(path: ConfigPath) -> str:
    """('nodes', 'r1', 'interfaces', 0, 'ip') -> nodes.r1.interfaces[0].ip"""
    text = ""
    for element in path:
        if isinstance(element, int):
            text += f"[{element}]"
        else:
            text += f".{element}" if text else str(element)
    return text


@dataclass
class IssueReport:
    source: Path | None = None
    issues: list[Issue] = field(default_factory=list)

    def error(self, message: str, position: Position | None = None, path: ConfigPath = ()) -> None:
        self.issues.append(Issue(ERROR, message, position, path))

    def warning(self, message: str, position: Position | None = None, path: ConfigPath = ()) -> None:
        self.issues.append(Issue(WARNING, message, position, path))

    def note(self, message: str, position: Position | None = None, path: ConfigPath = ()) -> None:
        self.issues.append(Issue(NOTE, message, position, path))

    @property
    def errors(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == ERROR]

    @property
    def warnings(self) -> list[Issue]:
        return [issue for issue in self.issues if issue.severity == WARNING]

    @property
    def valid(self) -> bool:
        return not self.errors

    def format_issue(self, issue: Issue) -> str:
        location = str(self.source) if self.source else ""
        if issue.position is not None:
            location = f"{location}:{issue.position.line}:{issue.position.column}".lstrip(":")
        where = format_path(issue.path)
        parts = [part for part in (location, issue.severity, where) if part]
        return f"{': '.join(parts)}: {issue.message}"

    def lines(self) -> list[str]:
        """Issues in file order (issues without a position first), then a summary."""
        ordered = sorted(
            self.issues,
            key=lambda issue: (issue.position is not None, issue.position or Position(0, 0)),
        )
        lines = [self.format_issue(issue) for issue in ordered]
        errors, warnings = len(self.errors), len(self.warnings)
        if errors:
            lines.append(f"[ERROR] validation failed: {errors} error(s), {warnings} warning(s)")
        else:
            lines.append(f"[OK] validation passed with {warnings} warning(s)")
        return lines

    def print(self) -> None:
        for line in self.lines():
            print(line)
