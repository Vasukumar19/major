"""PatchForge V2 execution foundation.

V2 keeps candidate state outside caller workspaces and makes verification
observations first-class artifacts.  It intentionally does not replace V1
until a controlled repair pilot validates the new path.
"""

from .candidates import Candidate, CandidateLedger, CandidateStatus, TestObservation, compute_candidate_score
from .agent_runner import V2AgentRunner, V2AgentRunResult
from .docker_executor import DockerSelectedTestExecutor, SELECTED_TEST_UNAVAILABLE
from .execution import CandidateWorkspaceService
from .report import CandidateRecord, V2RepairReport
from .runner import V2RepairRunner, V2RunnerConfig
from .verification import LocalSelectedTestExecutor, SelectedTestExecutor, accepts_residual_candidate
from .workspaces import OwnedWorkspace, WorkspaceManager, WorkspaceViolation

__all__ = [
    "Candidate",
    "CandidateLedger",
    "CandidateRecord",
    "CandidateStatus",
    "CandidateWorkspaceService",
    "DockerSelectedTestExecutor",
    "LocalSelectedTestExecutor",
    "OwnedWorkspace",
    "SELECTED_TEST_UNAVAILABLE",
    "SelectedTestExecutor",
    "TestObservation",
    "V2AgentRunResult",
    "V2AgentRunner",
    "V2RepairReport",
    "V2RepairRunner",
    "V2RunnerConfig",
    "WorkspaceManager",
    "WorkspaceViolation",
    "accepts_residual_candidate",
    "compute_candidate_score",
]
