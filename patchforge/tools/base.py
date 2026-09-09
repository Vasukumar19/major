"""Core tool interfaces and registry for PatchForge v0.3."""
from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass
class ToolResult:
    """Standard output schema for all PatchForge tools."""
    tool_name: str
    status: str  # "SUCCESS", "ERROR", "TIMEOUT", "SKIPPED"
    data: dict[str, Any] = field(default_factory=dict)
    message: str = ""
    error: str = ""
    raw_output: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "tool_name": self.tool_name,
            "status": self.status,
            "data": self.data,
            "message": self.message,
            "error": self.error,
            "raw_output": self.raw_output,
        }

    def summary(self) -> str:
        if self.status == "SUCCESS":
            return self.message or f"Tool {self.tool_name} completed successfully."
        return self.error or f"Tool {self.tool_name} returned status {self.status}."


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)
    call_id: str = ""


class Tool(abc.ABC):
    """Abstract base class for all PatchForge debugging tools."""

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = field(default_factory=dict)

    @abc.abstractmethod
    def execute(self, args: dict[str, Any], state: Any = None) -> ToolResult:
        """Execute the tool with given arguments against current agent state/environment."""
        raise NotImplementedError

    def schema(self) -> dict[str, Any]:
        """Returns OpenAI/OpenRouter compatible function tool schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters or {
                    "type": "object",
                    "properties": {},
                    "required": [],
                },
            },
        }


class ToolRegistry:
    """Registry managing available tools and dispatching invocations."""

    def __init__(self):
        self._tools: dict[str, Tool] = {}

    def register(self, tool: Tool) -> None:
        self._tools[tool.name] = tool

    def get(self, name: str) -> Tool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[Tool]:
        return list(self._tools.values())

    def schemas(self) -> list[dict[str, Any]]:
        return [tool.schema() for tool in self._tools.values()]

    def execute(self, tool_call: ToolCall, state: Any = None) -> ToolResult:
        tool = self.get(tool_call.name)
        if not tool:
            return ToolResult(
                tool_name=tool_call.name,
                status="ERROR",
                error=f"Tool '{tool_call.name}' is not registered in ToolRegistry.",
            )
        try:
            return tool.execute(tool_call.arguments, state=state)
        except Exception as e:
            return ToolResult(
                tool_name=tool_call.name,
                status="ERROR",
                error=f"Exception executing {tool_call.name}: {str(e)}",
            )
