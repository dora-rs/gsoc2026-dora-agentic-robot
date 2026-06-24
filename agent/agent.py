"""Agent loop + AgentConfig (Week 4) — mirrors `octos_agent::agent::Agent`.

The `Agent` runs the classic tool-calling loop:

    user message
        └─> provider.chat(messages, tool_schema) ──┐
                                                    │ finish_reason == "stop"?  ─> return text
                                                    │ else: execute each tool_call
                                                    │       append results, loop
                                                    └─ bounded by max_iterations / max_timeout

Week 4 scope is the *core*: the loop, `AgentConfig`, and self-contained tool
execution through the `ToolRegistry` (each tool is a `Tool` object, so the agent
executes it directly — no external bridge needed yet). Per-tool execution is
bounded by `tool_timeout_secs`. The richer machinery (skills, DOT-graph
pipelines, loop detection, safety hooks, context compaction) layers on in later
weeks; the loop here is deliberately small and forward-compatible.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from dataclasses import dataclass

from .provider import LlmProvider, ChatConfig
from .tool import ToolRegistry, ToolResult

DEFAULT_SYSTEM_PROMPT = (
    "You are a robot control agent operating a UR5e 6-DOF arm with a Robotiq "
    "2F-85 gripper in MuJoCo simulation. Use the available tools to inspect "
    "state and command motion. Read joint positions before planning, and wait "
    "for each motion to complete before issuing the next."
)


@dataclass
class AgentConfig:
    """Agent configuration — mirrors `octos_agent::agent::AgentConfig`.

    - `max_iterations`   — hard cap on LLM turns per message (loop backstop).
    - `max_timeout_secs` — wall-clock budget for the whole message.
    - `tool_timeout_secs`— per-tool execution budget; a tool that exceeds it is
                           abandoned and reported as an error to the model.
    - `temperature`      — sampling temperature passed through to the provider.
    """

    max_iterations: int = 50
    max_timeout_secs: float = 600.0
    tool_timeout_secs: float = 600.0
    temperature: float = 0.1


class Agent:
    """Octos-pattern agent with a tool-calling loop."""

    def __init__(
        self,
        provider: LlmProvider,
        registry: ToolRegistry,
        config: AgentConfig | None = None,
        system_prompt: str = DEFAULT_SYSTEM_PROMPT,
    ):
        self._provider = provider
        self._registry = registry
        self._config = config or AgentConfig()
        self._system_prompt = system_prompt

    @property
    def config(self) -> AgentConfig:
        return self._config

    def process_message(self, user_message: str, verbose: bool = False) -> str:
        """Run the agent loop for a user message and return the final text."""
        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt},
            {"role": "user", "content": user_message},
        ]
        chat_config = ChatConfig(temperature=self._config.temperature)
        deadline = time.monotonic() + self._config.max_timeout_secs
        final_response = "(no final response)"

        if verbose:
            print(f"\n>>> User: {user_message}")
            print(f"  provider={self._provider.name()} tools={self._registry.active_tool_names()}")

        for iteration in range(1, self._config.max_iterations + 1):
            if time.monotonic() > deadline:
                if verbose:
                    print(f"  [timeout] exceeded {self._config.max_timeout_secs}s")
                final_response = f"(timeout after {iteration - 1} iterations)"
                break

            tools_schema = self._registry.to_openai_schema()
            try:
                response = self._provider.chat(messages, tools_schema, chat_config)
            except Exception as exc:  # provider failure ends the loop cleanly
                if verbose:
                    print(f"  [error] provider call failed: {exc}")
                final_response = f"(provider error: {exc})"
                break

            # Record the assistant turn.
            assistant_msg: dict = {"role": "assistant"}
            if response.content:
                assistant_msg["content"] = response.content
            if response.tool_calls:
                assistant_msg["tool_calls"] = response.tool_calls
            messages.append(assistant_msg)

            # Done when the model stops (or asks for no tools).
            if response.finish_reason == "stop" or not response.tool_calls:
                if response.content:
                    final_response = response.content
                if verbose:
                    print(f">>> Agent: {final_response}")
                break

            # Execute each requested tool and feed results back.
            for tc in response.tool_calls:
                result = self._execute_tool_call(tc, verbose=verbose)
                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.get("id", ""),
                    "content": result,
                })

        if verbose:
            metrics = self._provider.export_metrics()
            if metrics:
                print(f"  [metrics] {metrics}")
        return final_response

    # --- tool execution ---------------------------------------------------

    def _execute_tool_call(self, tool_call: dict, verbose: bool = False) -> str:
        """Execute one OpenAI-style tool call, return a JSON string result.

        All failure modes (unknown tool, bad JSON arguments, tool exception,
        timeout) are converted into an `{"error": ...}` payload so the model can
        observe and recover rather than the loop crashing.
        """
        fn = tool_call.get("function", {})
        name = fn.get("name", "")
        raw_args = fn.get("arguments", "{}")

        try:
            args = raw_args if isinstance(raw_args, dict) else json.loads(raw_args or "{}")
        except (json.JSONDecodeError, TypeError) as exc:
            return json.dumps({"error": f"invalid tool arguments: {exc}"})

        tool = self._registry.get(name)
        if tool is None:
            return json.dumps({"error": f"unknown tool: {name!r}"})

        if verbose:
            print(f"  [tool] {name}({json.dumps(args, separators=(',', ':'))})")

        try:
            result = self._run_with_timeout(tool.execute, args)
        except FuturesTimeout:
            return json.dumps({"error": f"tool {name!r} timed out after "
                                        f"{self._config.tool_timeout_secs}s"})
        except Exception as exc:  # tool raised — surface it to the model
            return json.dumps({"error": f"tool {name!r} failed: {exc}"})

        if not isinstance(result, ToolResult):
            return json.dumps({"error": f"tool {name!r} did not return a ToolResult"})
        if not result.success:
            return json.dumps({"error": result.output})
        return result.output

    def _run_with_timeout(self, func, args: dict) -> ToolResult:
        """Run `func(args)` under the configured per-tool timeout."""
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(func, args)
            return future.result(timeout=self._config.tool_timeout_secs)
