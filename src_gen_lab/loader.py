"""Load the SK-F syntax dictionary (dictionary.yml).

The dictionary is data: value types, sections with their allowed keys, the
keys of the previous syntax and a few settings. This module reads it, checks
that it is consistent and returns it as a ``Dictionary`` object used by the
Lexer, the Parser and the translator.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from src_gen_lab.issues import DictionaryError

DICTIONARY_PATH = Path(__file__).with_name("dictionary.yml")

# Recognizers implemented by the Lexer for the "kind" of a type.
SCALAR_KINDS = frozenset({
    "string",
    "ipv4_address",
    "ipv4_interface",
    "ipv4_network",
    "integer",
    "number",
    "boolean",
    "ospf_area",
    "relative_path",
})

SHAPES = ("type", "list", "map", "section")
KEY_ATTRIBUTES = frozenset(SHAPES) | {"required", "default", "min", "router_only", "description"}


@dataclass(frozen=True)
class ValueType:
    name: str
    kind: str
    description: str
    pattern: re.Pattern[str] | None = None
    enum: tuple[str, ...] | None = None
    min: int | float | None = None
    max: int | float | None = None
    min_exclusive: int | float | None = None


@dataclass(frozen=True)
class Key:
    """An allowed key of a section."""

    name: str
    shape: str  # "type", "list", "map" or "section"
    target: str  # a type name or a section name
    description: str = ""
    required: bool = False
    default: Any = None
    min_items: int = 0
    router_only: bool = False


@dataclass(frozen=True)
class Section:
    name: str
    description: str
    keys: dict[str, Key]


@dataclass(frozen=True)
class Dictionary:
    types: dict[str, ValueType]
    sections: dict[str, Section]
    legacy: dict[str, str]
    settings: dict[str, Any]
    vocabulary: frozenset[str]  # every key name of every section
    identifier_keys: frozenset[str]  # keys whose value maps user-chosen names to sections

    @property
    def root(self) -> Section:
        return self.sections["root"]

    def is_section(self, name: str) -> bool:
        return name in self.sections

    def sections_with_key(self, word: str) -> list[Section]:
        return [section for section in self.sections.values() if word in section.keys]


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise DictionaryError(f"dictionary: {message}")


def _load_type(name: str, raw: Any) -> ValueType:
    _require(isinstance(raw, dict), f"type '{name}' must be a mapping")
    kind = raw.get("kind")
    _require(kind in SCALAR_KINDS, f"type '{name}' has unknown kind '{kind}'")
    pattern = raw.get("pattern")
    enum = raw.get("enum")
    _require(enum is None or (isinstance(enum, list) and enum), f"type '{name}': enum must be a non-empty list")
    return ValueType(
        name=name,
        kind=kind,
        description=str(raw.get("description", name)),
        pattern=re.compile(pattern) if pattern else None,
        enum=tuple(str(value) for value in enum) if enum else None,
        min=raw.get("min"),
        max=raw.get("max"),
        min_exclusive=raw.get("min_exclusive"),
    )


def _load_key(section: str, name: str, raw: Any) -> Key:
    where = f"key '{section}.{name}'"
    _require(isinstance(raw, dict), f"{where} must be a mapping")
    unknown = set(raw) - KEY_ATTRIBUTES
    _require(not unknown, f"{where} has unknown attributes: {', '.join(sorted(unknown))}")
    shapes = [shape for shape in SHAPES if shape in raw]
    _require(len(shapes) == 1, f"{where} needs exactly one of: {', '.join(SHAPES)}")
    shape = shapes[0]
    return Key(
        name=name,
        shape=shape,
        target=str(raw[shape]),
        description=str(raw.get("description", "")),
        required=bool(raw.get("required", False)),
        default=raw.get("default"),
        min_items=int(raw.get("min", 0)),
        router_only=bool(raw.get("router_only", False)),
    )


def _check_references(types: dict[str, ValueType], sections: dict[str, Section]) -> None:
    _require("root" in sections, "the 'root' section is missing")
    _require("name" in types, "the 'name' type is missing: the Lexer uses it for user-chosen names")
    for section in sections.values():
        for key in section.keys.values():
            where = f"key '{section.name}.{key.name}'"
            if key.shape == "type":
                _require(key.target in types, f"{where} refers to unknown type '{key.target}'")
            elif key.shape in ("map", "section"):
                _require(key.target in sections, f"{where} refers to unknown section '{key.target}'")
            else:
                _require(
                    key.target in types or key.target in sections,
                    f"{where} refers to unknown type or section '{key.target}'",
                )


def _check_settings(settings: dict[str, Any]) -> None:
    images = settings.get("images")
    _require(isinstance(images, dict) and {"host", "router"} <= set(images), "settings.images needs host and router")
    capture = settings.get("capture")
    _require(isinstance(capture, dict) and {"node", "image", "port"} <= set(capture), "settings.capture is incomplete")


def load_dictionary(path: Path = DICTIONARY_PATH) -> Dictionary:
    """Read and check a dictionary file. Raises DictionaryError if it is inconsistent."""
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise DictionaryError(f"cannot read the dictionary {path}: {exc}") from exc
    _require(isinstance(raw, dict), "the file must be a mapping")
    for part in ("types", "sections", "legacy", "settings"):
        _require(isinstance(raw.get(part), dict), f"'{part}' must be a mapping")

    types = {name: _load_type(name, value) for name, value in raw["types"].items()}

    sections: dict[str, Section] = {}
    for name, value in raw["sections"].items():
        _require(isinstance(value, dict) and isinstance(value.get("keys"), dict), f"section '{name}' needs keys")
        keys = {key: _load_key(name, key, spec) for key, spec in value["keys"].items()}
        sections[name] = Section(name=name, description=str(value.get("description", name)), keys=keys)

    _check_references(types, sections)
    _check_settings(raw["settings"])

    vocabulary = frozenset(key for section in sections.values() for key in section.keys)
    identifier_keys = frozenset(
        key.name for section in sections.values() for key in section.keys.values() if key.shape == "map"
    )
    for word in identifier_keys:
        shapes = {key.shape for section in sections.values() for key in section.keys.values() if key.name == word}
        _require(shapes == {"map"}, f"key '{word}' must be a map in every section that uses it")

    legacy = {str(word): str(hint) for word, hint in raw["legacy"].items()}
    overlap = vocabulary & set(legacy)
    _require(not overlap, f"legacy keys also in the vocabulary: {', '.join(sorted(overlap))}")

    return Dictionary(
        types=types,
        sections=sections,
        legacy=legacy,
        settings=raw["settings"],
        vocabulary=vocabulary,
        identifier_keys=identifier_keys,
    )


@lru_cache(maxsize=1)
def default_dictionary() -> Dictionary:
    """The dictionary shipped with SK-F, loaded once."""
    return load_dictionary()
