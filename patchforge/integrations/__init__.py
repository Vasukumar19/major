"""Integrations module for PatchForge AI."""
from patchforge.integrations.direct import DirectWorkItemIngestionHandler
from patchforge.integrations.github import (
    GitHubWebhookHandler,
    IngestionIdempotencyTracker,
    fetch_github_issue,
    verify_github_signature,
)
from patchforge.integrations.work_item import PatchForgeWorkItem

__all__ = [
    "PatchForgeWorkItem",
    "GitHubWebhookHandler",
    "IngestionIdempotencyTracker",
    "verify_github_signature",
    "fetch_github_issue",
    "DirectWorkItemIngestionHandler",
]
