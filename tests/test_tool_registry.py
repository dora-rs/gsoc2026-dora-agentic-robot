"""Unit tests for the ToolRegistry — LRU lifecycle + base-tool pinning (Week 4).

No LLM, no dora, no MuJoCo required.
"""

from agent.tool import FunctionTool, Tool, ToolRegistry, ToolResult


def _tool(name: str, tags: list[str] | None = None) -> FunctionTool:
    return FunctionTool(
        name=name,
        description=f"{name} tool",
        input_schema={"type": "object", "properties": {}},
        func=lambda args: ToolResult(output=f"ran {name}"),
        tags=tags or [],
    )


def test_register_and_discovery():
    reg = ToolRegistry()
    reg.register(_tool("a"))
    reg.register(_tool("b"))
    assert reg.count() == 2
    assert set(reg.names()) == {"a", "b"}
    assert reg.get("a").name() == "a"
    assert reg.get("missing") is None


def test_register_replaces_same_name():
    reg = ToolRegistry()
    reg.register(_tool("a"))
    reg.register(_tool("a"))
    assert reg.count() == 1


def test_unregister_removes_everywhere():
    reg = ToolRegistry()
    reg.register(_tool("a"))
    reg.set_base_tools(["a"])
    reg.unregister("a")
    assert reg.count() == 0
    assert reg.base_tools() == []
    assert "a" not in reg.active_tool_names()


def test_find_by_tag():
    reg = ToolRegistry()
    reg.register(_tool("a", tags=["transport"]))
    reg.register(_tool("b", tags=["motion"]))
    reg.register(_tool("c", tags=["transport", "read"]))
    found = {t.name() for t in reg.find_by_tag("transport")}
    assert found == {"a", "c"}


def test_openai_schema_shape():
    reg = ToolRegistry()
    reg.register(_tool("a"))
    schema = reg.to_openai_schema()
    assert len(schema) == 1
    entry = schema[0]
    assert entry["type"] == "function"
    assert entry["function"]["name"] == "a"
    assert entry["function"]["description"] == "a tool"
    assert entry["function"]["parameters"] == {"type": "object", "properties": {}}


def test_lru_eviction_beyond_max_active():
    reg = ToolRegistry(max_active=2)
    for n in ("a", "b", "c"):
        reg.register(_tool(n))
    # Most-recently-registered/used win; "a" (oldest, untouched) is evicted.
    reg.get("b")
    reg.get("c")
    active = reg.active_tool_names()
    assert len(active) == 2
    assert "a" not in active
    assert set(active) == {"b", "c"}


def test_get_marks_most_recently_used():
    reg = ToolRegistry(max_active=2)
    for n in ("a", "b", "c"):
        reg.register(_tool(n))
    # Touch "a" so it becomes most-recent; now "b" (now oldest) drops out.
    reg.get("a")
    active = reg.active_tool_names()
    assert "a" in active
    assert "b" not in active


def test_base_tools_pinned_against_eviction():
    reg = ToolRegistry(max_active=2)
    for n in ("base", "x", "y", "z"):
        reg.register(_tool(n))
    reg.set_base_tools(["base"])
    # Use the non-base tools so "base" is the least-recently-used overall.
    reg.get("x")
    reg.get("y")
    reg.get("z")
    active = reg.active_tool_names()
    # "base" survives despite being LRU; total never exceeds max_active.
    assert "base" in active
    assert len(active) == 2


def test_base_tools_always_in_schema_even_beyond_max_active():
    reg = ToolRegistry(max_active=1)
    reg.register(_tool("base1"))
    reg.register(_tool("base2"))
    reg.register(_tool("other"))
    reg.set_base_tools(["base1", "base2"])
    names = {e["function"]["name"] for e in reg.to_openai_schema()}
    # Both base tools appear even though max_active is 1.
    assert {"base1", "base2"} <= names


def test_max_active_must_be_positive():
    import pytest
    with pytest.raises(ValueError):
        ToolRegistry(max_active=0)


def test_tool_is_abstract():
    import pytest
    with pytest.raises(TypeError):
        Tool()  # cannot instantiate the ABC directly
