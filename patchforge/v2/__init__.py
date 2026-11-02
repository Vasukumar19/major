"""PatchForge V2 execution foundation.

V2 keeps candidate state outside caller workspaces and makes verification
observations first-class artifacts.  It intentionally does not replace V1
until a controlled repair pilot validates the new path.
"""

from .candidates import Candidate, CandidateLedger, CandidateStatus, TestObservation
from .agent_runner import V2AgentRunner, V2AgentRunResult
from .workspaces import OwnedWorkspace, WorkspaceManager, WorkspaceViolation

__all__ = [
    "Candidate",
    "CandidateLedger",
    "CandidateStatus",
    "OwnedWorkspace",
    "V2AgentRunResult",
    "V2AgentRunner",
    "TestObservation",
    "WorkspaceManager",
    "WorkspaceViolation",
]
