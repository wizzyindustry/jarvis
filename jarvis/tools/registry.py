"""Tool definitions, argument validation, and the gate every call passes through."""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Callable

from ..safety.policy import Decision, Level

log = logging.getLogger(__name__)


class ToolError(Exception):
    """A tool failed in a way worth telling the user about."""


@dataclass
class Tool:
    name: str
    description: str
    params: dict[str, dict[str, Any]]          # JSON-schema properties
    required: list[str]
    level: Level
    func: Callable[..., str]
    # Human-readable description of a specific call, shown in approval dialogs
    describe: Callable[[dict[str, Any]], str] | None = None
    # Optional per-call level (e.g. paths outside allowed folders -> NEVER)
    decide: Callable[[dict[str, Any]], Decision] | None = None
    # Output contains outside text (files, email, web): later free actions get escalated
    untrusted_output: bool = False
    # Expose to the AI model (some tools are router-only)
    for_model: bool = True

    def schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": {"type": "object", "properties": self.params, "required": self.required},
            },
        }

    def summary(self, args: dict[str, Any]) -> str:
        if self.describe:
            try:
                return self.describe(args)
            except Exception:
                pass
        return f"{self.name}({', '.join(f'{k}={v!r}' for k, v in args.items())})"


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def add(self, tool: Tool) -> None:
        if tool.name in self.tools:
            raise ValueError(f"duplicate tool {tool.name}")
        self.tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self.tools.get(name)

    def model_schemas(self) -> list[dict[str, Any]]:
        return [t.schema() for t in self.tools.values() if t.for_model]


_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool}


def validate_args(tool: Tool, args: Any) -> dict[str, Any]:
    """Check AI-produced arguments against the tool's schema. Raises ToolError with a short reason."""
    if not isinstance(args, dict):
        raise ToolError("arguments must be an object")
    clean: dict[str, Any] = {}
    for key in args:
        if key not in tool.params:
            raise ToolError(f"unknown argument '{key}'")
    for key in tool.required:
        if key not in args or args[key] in (None, ""):
            raise ToolError(f"missing argument '{key}'")
    for key, val in args.items():
        spec = tool.params[key]
        typ = spec.get("type", "string")
        if typ == "integer" and isinstance(val, str) and val.strip().lstrip("-").isdigit():
            val = int(val.strip())
        if typ == "number" and isinstance(val, str):
            try:
                val = float(val)
            except ValueError:
                pass
        py = _TYPES.get(typ)
        if py is not None and (not isinstance(val, py) or (typ in ("integer", "number") and isinstance(val, bool))):
            raise ToolError(f"argument '{key}' must be {typ}")
        if "enum" in spec and val not in spec["enum"]:
            raise ToolError(f"argument '{key}' must be one of {spec['enum']}")
        if isinstance(val, str) and len(val) > spec.get("maxLength", 4000):
            raise ToolError(f"argument '{key}' is too long")
        if typ in ("integer", "number"):
            if "minimum" in spec and val < spec["minimum"] or "maximum" in spec and val > spec["maximum"]:
                raise ToolError(f"argument '{key}' is out of range")
        clean[key] = val
    return clean
