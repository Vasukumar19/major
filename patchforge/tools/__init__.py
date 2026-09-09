"""PatchForge v0.3 Tool System."""
from __future__ import annotations

from patchforge.tools.base import Tool, ToolCall, ToolRegistry, ToolResult
from patchforge.tools.editor import ApplyPatchTool, GitDiffTool, ReadFileTool, ReadTestTool
from patchforge.tools.execution import InspectFailureTool, RunReproductionTool, RunTargetedTestTool
from patchforge.tools.graph import InspectGitHistoryTool, InspectGraphTool
from patchforge.tools.search import FindReferencesTool, FindSymbolTool, SearchCodeTool, SearchExactTool


def create_default_tool_registry(repo_dir: str = "", repograph_adapter=None, tester=None) -> ToolRegistry:
    """Instantiate and register all 13 core PatchForge v0.3 tools."""
    registry = ToolRegistry()
    registry.register(SearchCodeTool(repo_dir=repo_dir))
    registry.register(SearchExactTool(repo_dir=repo_dir))
    registry.register(FindSymbolTool(repo_dir=repo_dir))
    registry.register(FindReferencesTool(repo_dir=repo_dir))
    registry.register(ReadFileTool(repo_dir=repo_dir))
    registry.register(ReadTestTool(repo_dir=repo_dir))
    registry.register(InspectGraphTool(repo_dir=repo_dir, repograph_adapter=repograph_adapter))
    registry.register(InspectGitHistoryTool(repo_dir=repo_dir))
    registry.register(RunTargetedTestTool(tester=tester))
    registry.register(RunReproductionTool())
    registry.register(InspectFailureTool())
    registry.register(ApplyPatchTool(repo_dir=repo_dir))
    registry.register(GitDiffTool())
    return registry


__all__ = [
    "Tool",
    "ToolResult",
    "ToolCall",
    "ToolRegistry",
    "SearchCodeTool",
    "SearchExactTool",
    "FindSymbolTool",
    "FindReferencesTool",
    "ReadFileTool",
    "ReadTestTool",
    "InspectGraphTool",
    "InspectGitHistoryTool",
    "RunTargetedTestTool",
    "RunReproductionTool",
    "InspectFailureTool",
    "ApplyPatchTool",
    "GitDiffTool",
    "create_default_tool_registry",
]
