"""GitHub External Application Integration for PatchForge AI.

Supports:
1. GitHub Webhook Event Ingestion (issues, issue_comment, ping).
2. HMAC-SHA256 signature verification (X-Hub-Signature-256).
3. Idempotency and replay attack prevention (X-GitHub-Delivery).
4. GitHub REST API issue ingestion.
5. Translation to normalized PatchForgeWorkItem.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import re
import time
import urllib.request
import urllib.error
from typing import Any, Dict, List, Optional, Set, Tuple

from patchforge.integrations.work_item import PatchForgeWorkItem

logger = logging.getLogger(__name__)


def verify_github_signature(payload_bytes: bytes, signature_header: Optional[str], secret: str) -> bool:
    """Verifies HMAC-SHA256 signature against webhook payload using constant-time comparison."""
    if not secret or not signature_header:
        return False

    prefix = "sha256="
    if not signature_header.startswith(prefix):
        return False

    received_sig = signature_header[len(prefix):].strip()
    expected_sig = hmac.new(
        key=secret.encode("utf-8"),
        msg=payload_bytes,
        digestmod=hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(received_sig, expected_sig)


class IngestionIdempotencyTracker:
    """In-memory idempotency store with TTL to prevent duplicate event execution."""

    def __init__(self, ttl_seconds: int = 86400):
        self.ttl_seconds = ttl_seconds
        self._seen: Dict[str, float] = {}

    def is_duplicate(self, delivery_id: str) -> bool:
        """Returns True if delivery_id was seen within the active TTL window."""
        if not delivery_id:
            return False
        self._prune()
        return delivery_id in self._seen

    def record_delivery(self, delivery_id: str) -> None:
        """Records a processed delivery_id with current timestamp."""
        if delivery_id:
            self._prune()
            self._seen[delivery_id] = time.time()

    def _prune(self) -> None:
        """Removes entries older than TTL."""
        now = time.time()
        expired = [k for k, t in self._seen.items() if now - t > self.ttl_seconds]
        for k in expired:
            del self._seen[k]


class GitHubWebhookHandler:
    """Processes incoming GitHub webhook requests securely into PatchForgeWorkItems."""

    SUPPORTED_ISSUE_ACTIONS: Set[str] = {"opened", "reopened", "labeled", "edited"}

    def __init__(
        self,
        webhook_secret: Optional[str] = None,
        require_signature: bool = True,
        idempotency_tracker: Optional[IngestionIdempotencyTracker] = None,
    ):
        self.webhook_secret = webhook_secret or os.environ.get("GITHUB_WEBHOOK_SECRET", "")
        self.require_signature = require_signature
        self.tracker = idempotency_tracker or IngestionIdempotencyTracker()

    def handle_payload(
        self,
        payload_bytes: bytes,
        headers: Dict[str, str],
    ) -> Tuple[bool, int, str, Optional[PatchForgeWorkItem]]:
        """Validates headers, verifies signature, deduplicates delivery, and maps to WorkItem.

        Returns (success, http_status_code, response_message, work_item_or_none).
        """
        # Case-insensitive header lookup
        hdr = {k.lower(): v for k, v in headers.items()}
        event_type = hdr.get("x-github-event", "").strip()
        delivery_id = hdr.get("x-github-delivery", "").strip()
        signature = hdr.get("x-hub-signature-256", "").strip()

        if not event_type:
            return False, 400, "Missing required header 'X-GitHub-Event'.", None

        # Handle GitHub ping event
        if event_type == "ping":
            return True, 200, "Webhook ping received successfully (pong).", None

        # Enforce delivery ID presence
        if not delivery_id:
            return False, 400, "Missing required header 'X-GitHub-Delivery'.", None

        # Check idempotency / replay prevention
        if self.tracker.is_duplicate(delivery_id):
            logger.info(f"Duplicate delivery event '{delivery_id}' ignored.")
            return True, 200, f"Delivery '{delivery_id}' already processed (idempotent ignore).", None

        # Verify signature if secret configured or required
        if self.webhook_secret:
            if not signature or not verify_github_signature(payload_bytes, signature, self.webhook_secret):
                logger.warning(f"Signature verification failed for delivery '{delivery_id}'.")
                return False, 401, "Invalid webhook signature (X-Hub-Signature-256).", None
        elif self.require_signature:
            return False, 401, "Webhook secret not configured, but signatures are required.", None

        # Parse JSON body
        try:
            payload = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            logger.warning(f"Failed to decode JSON payload: {e}")
            return False, 400, f"Malformed JSON payload: {e}", None

        # Process supported events
        if event_type == "issues":
            action = payload.get("action", "")
            if action not in self.SUPPORTED_ISSUE_ACTIONS:
                return True, 200, f"Issue action '{action}' ignored (not actionable for repair).", None

            issue_data = payload.get("issue", {})
            repo_data = payload.get("repository", {})

            repo_name = repo_data.get("full_name") or repo_data.get("name") or ""
            issue_num = str(issue_data.get("number", ""))
            title = issue_data.get("title", "")
            body = issue_data.get("body", "") or ""
            labels = [lbl.get("name", "") for lbl in issue_data.get("labels", []) if isinstance(lbl, dict)]
            default_branch = repo_data.get("default_branch", "main")

            # Extract reproduction command / test if provided in markdown block
            repro_test = self._extract_reproduction_test(body)

            work_item = PatchForgeWorkItem(
                source_application="github",
                source_event_id=delivery_id,
                repository=repo_name,
                issue_id=f"GH-{issue_num}" if issue_num else "",
                title=title,
                problem_statement=body,
                base_commit=default_branch,
                reproduction_test=repro_test,
                labels=labels,
                metadata={
                    "event_type": event_type,
                    "action": action,
                    "sender": payload.get("sender", {}).get("login", ""),
                    "issue_url": issue_data.get("html_url", ""),
                },
                raw_payload={"repository": repo_name, "issue_number": issue_num, "action": action},
            )

            is_valid, err_msg = work_item.validate()
            if not is_valid:
                return False, 422, f"Unprocessable work item: {err_msg}", None

            self.tracker.record_delivery(delivery_id)
            return True, 200, "Issue work item successfully created.", work_item

        elif event_type == "issue_comment":
            action = payload.get("action", "")
            if action != "created":
                return True, 200, f"Comment action '{action}' ignored.", None

            comment_body = payload.get("comment", {}).get("body", "")
            # Only trigger repair if comment mentions /patchforge repair command
            if "/patchforge repair" not in comment_body.lower() and "/fix" not in comment_body.lower():
                return True, 200, "Comment ignored (no PatchForge command trigger found).", None

            issue_data = payload.get("issue", {})
            repo_data = payload.get("repository", {})
            repo_name = repo_data.get("full_name") or repo_data.get("name") or ""
            issue_num = str(issue_data.get("number", ""))

            work_item = PatchForgeWorkItem(
                source_application="github",
                source_event_id=delivery_id,
                repository=repo_name,
                issue_id=f"GH-{issue_num}",
                title=issue_data.get("title", ""),
                problem_statement=f"{issue_data.get('body', '')}\n\nAdditional comment instruction:\n{comment_body}",
                base_commit=repo_data.get("default_branch", "main"),
                labels=[lbl.get("name", "") for lbl in issue_data.get("labels", []) if isinstance(lbl, dict)],
                metadata={
                    "event_type": event_type,
                    "action": action,
                    "comment_author": payload.get("comment", {}).get("user", {}).get("login", ""),
                },
            )
            is_valid, err_msg = work_item.validate()
            if not is_valid:
                return False, 422, f"Unprocessable work item: {err_msg}", None

            self.tracker.record_delivery(delivery_id)
            return True, 200, "Comment repair work item successfully created.", work_item

        return True, 200, f"Event '{event_type}' ignored (not configured for automated repair).", None

    @classmethod
    def _extract_reproduction_test(cls, body: str) -> str:
        """Extracts candidate test command from markdown code blocks or keywords."""
        if not body:
            return ""
        # Match `pytest tests/...` or `python -m pytest ...`
        match = re.search(r"`(pytest\s+[^\`\n]+|python\s+-m\s+pytest\s+[^\`\n]+)`", body)
        if match:
            return match.group(1).strip()
        return ""


def fetch_github_issue(
    owner: str,
    repo: str,
    issue_number: int,
    token: Optional[str] = None,
) -> Tuple[bool, str, Optional[PatchForgeWorkItem]]:
    """Fetches an issue directly from the GitHub REST API and converts to a WorkItem."""
    if not owner or not repo or issue_number <= 0:
        return False, "Invalid owner, repo, or issue_number.", None

    url = f"https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}"
    headers = {
        "User-Agent": "PatchForge-AI/1.9",
        "Accept": "application/vnd.github.v3+json",
    }
    auth_token = token or os.environ.get("GITHUB_TOKEN", "")
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return False, f"GitHub API error {e.code}: {e.reason}", None
    except Exception as e:
        return False, f"Failed to connect to GitHub API: {e}", None

    title = data.get("title", "")
    body = data.get("body", "") or ""
    labels = [l.get("name", "") for l in data.get("labels", []) if isinstance(l, dict)]

    work_item = PatchForgeWorkItem(
        source_application="github_api",
        source_event_id=f"GH-{owner}-{repo}-{issue_number}",
        repository=f"{owner}/{repo}",
        issue_id=f"GH-{issue_number}",
        title=title,
        problem_statement=body,
        labels=labels,
        metadata={"html_url": data.get("html_url", ""), "state": data.get("state", "")},
    )

    is_valid, err_msg = work_item.validate()
    if not is_valid:
        return False, err_msg, None

    return True, "Issue fetched successfully.", work_item
