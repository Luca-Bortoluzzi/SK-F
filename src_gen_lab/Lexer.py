#!/usr/bin/env python3
"""Lexical analysis of an SK-F lab file.

The Lexer reads the YAML text and checks its words and literals against the
dictionary, without looking at where they appear:

* YAML syntax errors and empty files;
* keys repeated in the same mapping (validator rule 2);
* every key is a word of the dictionary (rule 1, with a suggestion) or, under
  a key such as ``nodes``, a user-chosen name (rule 4); keys of the previous
  syntax are reported with what replaces them;
* every value is classified into the dictionary types it belongs to (ip, cidr,
  name, ...); a value that looks like an address but is not valid is an error,
  and so is an IPv6 address: addresses are IPv4 only.

The result is a tree of tokens with their position in the file. Rejected keys
are left out of the tree, so the Parser never sees them.
"""

from __future__ import annotations

import difflib
import ipaddress
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Union

try:
    import yaml
except ImportError:
    print("Error: PyYAML is not installed. Run: pip install pyyaml", file=sys.stderr)
    raise

from src_gen_lab.issues import ConfigPath, IssueReport, Position
from src_gen_lab.loader import Dictionary, ValueType

YAML_EXTENSIONS = (".yml", ".yaml")

# A string meant as an IPv4 address: four dotted numbers, or numbers with a prefix.
_ADDRESS_LIKE = re.compile(r"^[0-9]+(\.[0-9]+){3}(/[0-9]+)?$|^[0-9.]+/[0-9]+$")


def _is_ipv6(value: str) -> bool:
    """An IPv6 address or network, with or without prefix."""
    if ":" not in value:
        return False
    try:
        ipaddress.IPv6Interface(value)
    except ValueError:
        return False
    return True


# ---------------------------------------------------------------------------
# Token tree
# ---------------------------------------------------------------------------


@dataclass
class Scalar:
    value: Any
    position: Position
    types: frozenset[str]  # dictionary types this literal belongs to
    malformed: bool = False  # already reported by the Lexer


@dataclass
class Entry:
    key: Any
    key_position: Position
    value: "Node"


@dataclass
class Mapping:
    position: Position
    entries: dict[Any, Entry] = field(default_factory=dict)
    rejected: list[Any] = field(default_factory=list)  # keys reported and removed by the Lexer


@dataclass
class Sequence:
    position: Position
    items: list["Node"] = field(default_factory=list)


Node = Union[Mapping, Sequence, Scalar]


# ---------------------------------------------------------------------------
# Literal recognizers, one per "kind" of the dictionary
# ---------------------------------------------------------------------------


def _is_int(value: Any) -> bool:
    return type(value) is int  # bool is a subclass of int


def _in_range(value: Any, value_type: ValueType) -> bool:
    if value_type.min is not None and value < value_type.min:
        return False
    if value_type.max is not None and value > value_type.max:
        return False
    if value_type.min_exclusive is not None and value <= value_type.min_exclusive:
        return False
    return True


def _ipv4(constructor: Any, value: Any, need_prefix: bool) -> bool:
    if not isinstance(value, str) or ("/" in value) != need_prefix:
        return False
    try:
        constructor(value)
    except ValueError:
        return False
    return True


def matches(value: Any, value_type: ValueType) -> bool:
    """True if ``value`` is a literal of ``value_type``."""
    kind = value_type.kind
    if kind == "string":
        if not isinstance(value, str):
            return False
        if value_type.pattern is not None and not value_type.pattern.fullmatch(value):
            return False
        return value_type.enum is None or value in value_type.enum
    if kind == "ipv4_address":
        return _ipv4(ipaddress.IPv4Address, value, need_prefix=False)
    if kind == "ipv4_interface":
        return _ipv4(ipaddress.IPv4Interface, value, need_prefix=True)
    if kind == "ipv4_network":
        return _ipv4(lambda text: ipaddress.IPv4Network(text, strict=True), value, need_prefix=True)
    if kind == "integer":
        return _is_int(value) and _in_range(value, value_type)
    if kind == "number":
        return (_is_int(value) or isinstance(value, float)) and _in_range(value, value_type)
    if kind == "boolean":
        return isinstance(value, bool)
    if kind == "ospf_area":
        if _is_int(value):
            return 0 <= value <= 4294967295
        return _ipv4(ipaddress.IPv4Address, value, need_prefix=False)
    if kind == "relative_path":
        return isinstance(value, str) and bool(value.strip()) and not value.startswith("/")
    raise AssertionError(f"no recognizer for kind '{kind}'")


def describe(value: Any) -> str:
    """How a value is shown in messages."""
    if value is None:
        return "nothing"
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return f"the number {value}"
    if isinstance(value, str):
        return f"'{value}'"
    return type(value).__name__


# ---------------------------------------------------------------------------
# Lexer
# ---------------------------------------------------------------------------


def resolve_config_path(argument: str, config_dir: Path) -> Path:
    candidate = Path(argument)
    if candidate.exists():
        return candidate

    for extension in YAML_EXTENSIONS:
        possible = config_dir / f"{argument}{extension}"
        if possible.exists():
            return possible

    raise FileNotFoundError(
        f"Configuration not found for '{argument}'. "
        f"Pass a valid YAML file or place {argument}.yml in {config_dir}"
    )


def _position(node: yaml.Node) -> Position:
    return Position(node.start_mark.line + 1, node.start_mark.column + 1)


class Lexer:
    def __init__(self, dictionary: Dictionary, report: IssueReport) -> None:
        self.dictionary = dictionary
        self.report = report
        self._constructor = yaml.SafeLoader("")
        self._open: set[int] = set()

    # -- entry points -------------------------------------------------------

    def tokenize_file(self, path: Path) -> Node | None:
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            self.report.error(f"cannot read {path}: {exc}")
            return None
        return self.tokenize_text(text)

    def tokenize_text(self, text: str) -> Node | None:
        try:
            root = yaml.compose(text, Loader=yaml.SafeLoader)
        except yaml.MarkedYAMLError as exc:
            mark = exc.problem_mark or exc.context_mark
            position = Position(mark.line + 1, mark.column + 1) if mark else None
            self.report.error(f"invalid YAML: {exc.problem or exc}", position)
            return None
        except yaml.YAMLError as exc:
            self.report.error(f"invalid YAML: {exc}")
            return None

        if root is None:
            self.report.error("the file is empty")
            return None
        return self._node(root, identifiers=False, path=())

    # -- tree ---------------------------------------------------------------

    def _node(self, node: yaml.Node, identifiers: bool, path: ConfigPath) -> Node:
        if isinstance(node, yaml.ScalarNode):
            return self._scalar(node, path)

        if id(node) in self._open:
            self.report.error("an alias refers to the element that contains it", _position(node), path)
            return Scalar(None, _position(node), frozenset(), malformed=True)
        self._open.add(id(node))
        try:
            if isinstance(node, yaml.SequenceNode):
                items = [self._node(item, False, path + (index,)) for index, item in enumerate(node.value)]
                return Sequence(_position(node), items)
            return self._mapping(node, identifiers, path)
        finally:
            self._open.discard(id(node))

    def _mapping(self, node: yaml.MappingNode, identifiers: bool, path: ConfigPath) -> Mapping:
        self._constructor.flatten_mapping(node)  # expand YAML merge keys (<<: *anchor)
        mapping = Mapping(_position(node))
        first_seen: dict[Any, Position] = {}

        for key_node, value_node in node.value:
            position = _position(key_node)
            if not isinstance(key_node, yaml.ScalarNode):
                self.report.error("a key must be a single word, not a list or mapping", position, path)
                continue
            key = self._constructor.construct_object(key_node, deep=True)

            if key in first_seen:
                self.report.error(
                    f"key {describe(key)} is repeated (first defined at line {first_seen[key].line})", position, path + (key,)
                )
                mapping.rejected.append(key)
                continue
            first_seen[key] = position

            accepted = self._identifier(key, position, path) if identifiers else self._word(key, position, path)
            if not accepted:
                mapping.rejected.append(key)
                continue

            children_are_identifiers = not identifiers and key in self.dictionary.identifier_keys
            mapping.entries[key] = Entry(key, position, self._node(value_node, children_are_identifiers, path + (key,)))
        return mapping

    # -- words --------------------------------------------------------------

    def _word(self, key: Any, position: Position, path: ConfigPath) -> bool:
        """A key chosen from the dictionary."""
        if isinstance(key, str) and key in self.dictionary.vocabulary:
            return True
        if isinstance(key, str) and key in self.dictionary.legacy:
            self.report.error(
                f"'{key}' belongs to the previous syntax: {self.dictionary.legacy[key]}", position, path + (key,)
            )
            return False

        message = f"unknown key {describe(key)}"
        if isinstance(key, str):
            close = difflib.get_close_matches(key, sorted(self.dictionary.vocabulary), n=1, cutoff=0.6)
            if close:
                message += f": did you mean '{close[0]}'?"
        self.report.error(message, position, path + (key,))
        return False

    def _identifier(self, key: Any, position: Position, path: ConfigPath) -> bool:
        """A name chosen by the user, such as a node name."""
        name_type = self.dictionary.types["name"]
        if matches(key, name_type):
            return True
        self.report.error(f"invalid name {describe(key)}: expected {name_type.description}", position, path + (key,))
        return False

    # -- literals -----------------------------------------------------------

    def _scalar(self, node: yaml.ScalarNode, path: ConfigPath) -> Scalar:
        position = _position(node)
        value = self._constructor.construct_object(node, deep=True)
        types = frozenset(
            name for name, value_type in self.dictionary.types.items() if matches(value, value_type)
        )
        malformed = False
        if isinstance(value, str) and _ADDRESS_LIKE.match(value):
            if not any(self.dictionary.types[name].kind.startswith("ipv4") for name in types):
                self.report.error(f"malformed IPv4 address '{value}'", position, path)
                malformed = True
        elif isinstance(value, str) and _is_ipv6(value):
            self.report.error(f"'{value}' is an IPv6 address: only IPv4 addresses are supported", position, path)
            malformed = True
        return Scalar(value, position, types, malformed)


def tokenize(path: Path, dictionary: Dictionary, report: IssueReport) -> Node | None:
    """Read a lab file and return its token tree (``None`` if it cannot be read)."""
    return Lexer(dictionary, report).tokenize_file(path)
