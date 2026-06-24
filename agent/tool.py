"""Tool abstraction and ToolRegistry (Week 4).

Mirrors the Octos `octos_agent::tools` design:

  - `Tool`      — abstract base: name, description, JSON-Schema for inputs, tags,
                  and a pure `execute(args) -> ToolResult`.
  - `ToolResult`— structured outcome of a tool call (output text + success flag).
  - `ToolRegistry` — registration, discovery, OpenAI function-calling schema
                  export, an **LRU lifecycle** (only the most-recently-used tools
                  stay "active" once the catalogue grows past `max_active`), and
                  **base-tool pinning** (pinned tools are never evicted, so the
                  model never loses its essential tools).

The LRU lifecycle keeps the tool schema small for large catalogues: an LLM does
not need 50 tool definitions in its context, only the base tools plus whatever
has been used recently. Everything here is pure Python — no LLM, no dora.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Callable


@dataclass
class ToolResult:
    """Result of a single tool execution."""

    output: str
    success: bool = True
    tokens_used: int = 0

    def __str__(self) -> str:  # convenient for feeding back into the message log
        return self.output


class Tool(ABC):
    """Abstract tool — mirrors the Octos `Tool` trait.

    A concrete tool exposes its name/description/input schema (for the LLM) and a
    side-effecting `execute`. `input_schema` must be a JSON Schema object so it
    can be dropped straight into an OpenAI function definition.
    """

    @abstractmethod
    def name(self) -> str:
        ...

    @abstractmethod
    def description(self) -> str:
        ...

    @abstractmethod
    def input_schema(self) -> dict:
        ...

    def tags(self) -> list[str]:
        """Optional tags for tag-based discovery/filtering. Default: none."""
        return []

    @abstractmethod
    def execute(self, args: dict) -> ToolResult:
        ...


class FunctionTool(Tool):
    """Adapter that turns a plain Python callable into a `Tool`.

    Lets tests and demos register a tool without subclassing — the callable
    receives the parsed `args` dict and returns either a `ToolResult` or a value
    that is stringified into one.
    """

    def __init__(
        self,
        name: str,
        description: str,
        input_schema: dict,
        func: Callable[[dict], object],
        tags: list[str] | None = None,
    ):
        self._name = name
        self._description = description
        self._input_schema = input_schema
        self._func = func
        self._tags = list(tags or [])

    def name(self) -> str:
        return self._name

    def description(self) -> str:
        return self._description

    def input_schema(self) -> dict:
        return self._input_schema

    def tags(self) -> list[str]:
        return list(self._tags)

    def execute(self, args: dict) -> ToolResult:
        result = self._func(args)
        if isinstance(result, ToolResult):
            return result
        return ToolResult(output=str(result))


class ToolRegistry:
    """Registry with LRU lifecycle and base-tool pinning.

    - `register` / `unregister` — manage the catalogue.
    - `set_base_tools` — pin tools that must always stay active (never evicted).
    - `get` — fetch a tool by name, marking it most-recently-used.
    - `active_tools` / `to_openai_schema` — the bounded set the model sees.
    - `find_by_tag` — tag-based discovery.

    `max_active` bounds how many tools appear in the exported schema. Base tools
    are always included (even beyond `max_active`); the remaining slots are filled
    with the most-recently-used non-base tools.
    """

    def __init__(self, max_active: int = 15):
        if max_active < 1:
            raise ValueError("max_active must be >= 1")
        self._tools: dict[str, Tool] = {}
        self._base_tools: set[str] = set()
        self._usage_order: list[str] = []  # oldest .. newest
        self._max_active = max_active

    # --- registration / discovery -----------------------------------------

    def register(self, tool: Tool) -> None:
        """Register (or replace) a tool by name."""
        name = tool.name()
        self._tools[name] = tool
        if name not in self._usage_order:
            self._usage_order.append(name)

    def unregister(self, name: str) -> None:
        self._tools.pop(name, None)
        self._base_tools.discard(name)
        if name in self._usage_order:
            self._usage_order.remove(name)

    def set_base_tools(self, names: list[str]) -> None:
        """Pin a set of tools as base tools (immune to LRU eviction)."""
        self._base_tools = set(names)

    def base_tools(self) -> list[str]:
        return sorted(self._base_tools)

    def names(self) -> list[str]:
        """All registered tool names (insertion order)."""
        return list(self._tools.keys())

    def count(self) -> int:
        return len(self._tools)

    def get(self, name: str) -> Tool | None:
        """Fetch a tool by name, marking it most-recently-used."""
        tool = self._tools.get(name)
        if tool is not None:
            self._touch(name)
        return tool

    def find_by_tag(self, tag: str) -> list[Tool]:
        """Discover registered tools carrying a given tag."""
        return [t for t in self._tools.values() if tag in t.tags()]

    # --- LRU lifecycle ----------------------------------------------------

    def _touch(self, name: str) -> None:
        if name in self._usage_order:
            self._usage_order.remove(name)
        self._usage_order.append(name)

    def active_tool_names(self) -> list[str]:
        """Names of the currently-active tools (base + most-recently-used).

        Base tools first (sorted, stable), then the most-recently-used non-base
        tools until `max_active` is reached.
        """
        active: list[str] = [n for n in sorted(self._base_tools) if n in self._tools]
        for name in reversed(self._usage_order):  # newest first
            if len(active) >= self._max_active:
                break
            if name not in active and name in self._tools:
                active.append(name)
        return active

    def active_tools(self) -> list[Tool]:
        """Active tools, respecting LRU eviction and base pinning."""
        return [self._tools[n] for n in self.active_tool_names()]

    # --- schema export ----------------------------------------------------

    def to_openai_schema(self) -> list[dict]:
        """Export the active tools as OpenAI function-calling definitions."""
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name(),
                    "description": tool.description(),
                    "parameters": tool.input_schema(),
                },
            }
            for tool in self.active_tools()
        ]
