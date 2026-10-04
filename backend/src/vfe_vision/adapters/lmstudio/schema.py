"""Turn a Pydantic model into a strict, self-contained JSON schema for grammar sampling.

llama.cpp grammars handle ``$ref`` poorly, and strict mode requires every property to be listed
as required with ``additionalProperties: false`` — so references are inlined and objects closed.
"""

from __future__ import annotations

import copy
from typing import Any

from pydantic import BaseModel

_DROPPED_KEYS = frozenset({"title", "default"})


def strict_json_schema(model: type[BaseModel]) -> dict[str, Any]:
    schema = model.model_json_schema()
    definitions: dict[str, Any] = schema.pop("$defs", {})
    result: dict[str, Any] = _close(_inline(schema, definitions))
    return result


def _inline(node: Any, definitions: dict[str, Any], *, names: bool = False) -> Any:
    """``names``: ``node`` maps property names to schemas; a property may be called ``title``
    or ``default`` like the schema keywords dropped elsewhere, so every name is kept."""
    if isinstance(node, dict):
        if names:
            return {k: _inline(v, definitions) for k, v in node.items()}
        if "$ref" in node:
            name = node["$ref"].rsplit("/", 1)[-1]
            resolved = _inline(copy.deepcopy(definitions[name]), definitions)
            extras = {k: v for k, v in node.items() if k != "$ref"}
            return {**resolved, **extras}
        return {
            k: _inline(v, definitions, names=k == "properties")
            for k, v in node.items()
            if k not in _DROPPED_KEYS
        }
    if isinstance(node, list):
        return [_inline(item, definitions) for item in node]
    return node


def _close(node: Any) -> Any:
    if isinstance(node, dict):
        closed = {k: _close(v) for k, v in node.items()}
        if closed.get("type") == "object" and "properties" in closed:
            closed["additionalProperties"] = False
            closed["required"] = list(closed["properties"])
        return closed
    if isinstance(node, list):
        return [_close(item) for item in node]
    return node


def field_guide(model: type[BaseModel]) -> str:
    """Human-readable guide of the expected fields, to put in the prompt.

    Grammar-constrained sampling forces the *shape* of the answer, but the model never sees the
    JSON schema itself: allowed values and per-field instructions must be spelled out in text.
    """
    return "\n".join(_guide_lines(strict_json_schema(model), indent=""))


_SIMPLE_TYPES = {"boolean": "true/false", "integer": "integer", "number": "number"}


def _describe_type(node: dict[str, Any]) -> str:
    if "enum" in node:
        return "one of: " + ", ".join(str(v) for v in node["enum"])
    if node.get("type") == "array":
        items = node.get("items", {})
        limit = f", max {node['maxItems']}" if "maxItems" in node else ""
        inner = "objects" if items.get("type") == "object" else _describe_type(items)
        return f"list of {inner}{limit}"
    if node.get("type") == "object":
        return "object"
    return _SIMPLE_TYPES.get(str(node.get("type")), "text")


def _guide_lines(node: dict[str, Any], indent: str) -> list[str]:
    lines: list[str] = []
    for name, prop in node.get("properties", {}).items():
        description = prop.get("description", "")
        lines.append(
            f"{indent}- {name} ({_describe_type(prop)})"
            + (f": {description}" if description else "")
        )
        if prop.get("type") == "array" and prop.get("items", {}).get("type") == "object":
            lines += _guide_lines(prop["items"], indent + "    ")
        elif prop.get("type") == "object":  # its own fields, one level deeper
            lines += _guide_lines(prop, indent + "    ")
    return lines
