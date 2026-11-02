"""Phase 5 Regression Tests: Application Input Ingestion & External System Integration.

Validates:
1. GitHub webhook signature verification (constant time HMAC-SHA256).
2. Idempotency tracker replay prevention and TTL pruning.
3. GitHub webhook event handling (ping, issues.opened, issue_comment commands).
4. GitHub webhook rejection cases (missing headers, invalid sig, malformed JSON, path traversal).
5. Direct REST API ingestion with Bearer token and X-API-Key authentication.
6. Direct REST API payload size limiting and Idempotency-Key deduplication.
7. Work item validation and conversion fidelity to Problem object.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
import pytest

from patchforge.integrations.direct import DirectWorkItemIngestionHandler
from patchforge.integrations.github import (
    GitHubWebhookHandler,
    IngestionIdempotencyTracker,
    verify_github_signature,
)
from patchforge.integrations.work_item import PatchForgeWorkItem


def _sign(payload: bytes, secret: str) -> str:
    """Helper to generate GitHub webhook sha256 signature."""
    mac = hmac.new(secret.encode("utf-8"), payload, hashlib.sha256).hexdigest()
    return f"sha256={mac}"


def test_github_signature_verification():
    secret = "test-webhook-secret-12345"
    payload = b'{"action":"opened","issue":{"number":42}}'

    valid_sig = _sign(payload, secret)
    assert verify_github_signature(payload, valid_sig, secret) is True

    # Tampered payload
    assert verify_github_signature(payload + b"tampered", valid_sig, secret) is False

    # Tampered signature
    assert verify_github_signature(payload, valid_sig + "bad", secret) is False

    # Missing prefix
    assert verify_github_signature(payload, "invalid_sig", secret) is False

    # Empty secret / signature
    assert verify_github_signature(payload, valid_sig, "") is False
    assert verify_github_signature(payload, None, secret) is False


def test_idempotency_tracker():
    tracker = IngestionIdempotencyTracker(ttl_seconds=1)
    delivery_1 = "deliv-uuid-001"
    delivery_2 = "deliv-uuid-002"

    assert not tracker.is_duplicate(delivery_1)
    tracker.record_delivery(delivery_1)
    assert tracker.is_duplicate(delivery_1)

    assert not tracker.is_duplicate(delivery_2)
    tracker.record_delivery(delivery_2)
    assert tracker.is_duplicate(delivery_2)

    # Let TTL expire
    time.sleep(1.1)
    assert not tracker.is_duplicate(delivery_1)


def test_github_webhook_ping_event():
    handler = GitHubWebhookHandler(webhook_secret="test-secret")
    payload = b'{"zen":"Design for failure."}'
    headers = {
        "X-GitHub-Event": "ping",
        "X-GitHub-Delivery": "ping-delivery-id",
    }
    success, status, msg, work_item = handler.handle_payload(payload, headers)
    assert success is True
    assert status == 200
    assert "pong" in msg.lower()
    assert work_item is None


def test_github_webhook_issues_opened_success():
    secret = "secret-issues-123"
    tracker = IngestionIdempotencyTracker(ttl_seconds=3600)
    handler = GitHubWebhookHandler(webhook_secret=secret, idempotency_tracker=tracker)

    payload_dict = {
        "action": "opened",
        "repository": {"full_name": "psf/requests", "default_branch": "main"},
        "issue": {
            "number": 2674,
            "title": "iter_lines returns full lines even with delimiter",
            "body": "When using `iter_lines(delimiter='\\n')`, chunks are split incorrectly.\nReproduction: `pytest tests/test_iter_lines.py`",
            "labels": [{"name": "bug"}, {"name": "streaming"}],
            "html_url": "https://github.com/psf/requests/issues/2674",
        },
        "sender": {"login": "reporter_alice"},
    }
    payload_bytes = json.dumps(payload_dict).encode("utf-8")
    sig = _sign(payload_bytes, secret)
    headers = {
        "X-GitHub-Event": "issues",
        "X-GitHub-Delivery": "delivery-req-2674",
        "X-Hub-Signature-256": sig,
    }

    success, status, msg, work_item = handler.handle_payload(payload_bytes, headers)
    assert success is True
    assert status == 200
    assert work_item is not None
    assert work_item.source_application == "github"
    assert work_item.repository == "psf/requests"
    assert work_item.issue_id == "GH-2674"
    assert work_item.reproduction_test == "pytest tests/test_iter_lines.py"
    assert "bug" in work_item.labels

    # Verify Problem conversion
    problem = work_item.to_problem()
    assert problem.instance_id == "GH-2674"
    assert problem.repo == "psf/requests"
    assert problem.fail_to_pass == ["pytest tests/test_iter_lines.py"]

    # Test idempotency deduplication on immediate repeat
    repeat_success, repeat_status, repeat_msg, repeat_item = handler.handle_payload(payload_bytes, headers)
    assert repeat_success is True
    assert repeat_status == 200
    assert "already processed" in repeat_msg
    assert repeat_item is None


def test_github_webhook_issue_comment_commands():
    secret = "secret-comment-456"
    handler = GitHubWebhookHandler(webhook_secret=secret)

    # 1. Comment without trigger is ignored
    ignored_dict = {
        "action": "created",
        "repository": {"full_name": "pallets/flask"},
        "issue": {"number": 100, "title": "Flask bug", "body": "Details"},
        "comment": {"body": "Thanks for reporting this!", "user": {"login": "bob"}},
    }
    ignored_bytes = json.dumps(ignored_dict).encode("utf-8")
    headers = {
        "X-GitHub-Event": "issue_comment",
        "X-GitHub-Delivery": "delivery-comment-1",
        "X-Hub-Signature-256": _sign(ignored_bytes, secret),
    }
    success, status, msg, item = handler.handle_payload(ignored_bytes, headers)
    assert success is True
    assert status == 200
    assert item is None
    assert "no PatchForge command" in msg

    # 2. Comment with /patchforge repair trigger is accepted
    triggered_dict = {
        "action": "created",
        "repository": {"full_name": "pallets/flask", "default_branch": "main"},
        "issue": {"number": 100, "title": "Flask bug", "body": "Details"},
        "comment": {"body": "/patchforge repair please inspect config", "user": {"login": "maintainer"}},
    }
    triggered_bytes = json.dumps(triggered_dict).encode("utf-8")
    headers["X-GitHub-Delivery"] = "delivery-comment-2"
    headers["X-Hub-Signature-256"] = _sign(triggered_bytes, secret)

    success, status, msg, item = handler.handle_payload(triggered_bytes, headers)
    assert success is True
    assert status == 200
    assert item is not None
    assert item.issue_id == "GH-100"
    assert "Additional comment instruction" in item.problem_statement


def test_github_webhook_rejections():
    handler = GitHubWebhookHandler(webhook_secret="sec")

    # Missing event header
    success, status, msg, _ = handler.handle_payload(b"{}", {"X-GitHub-Delivery": "1"})
    assert status == 400

    # Missing delivery header
    success, status, msg, _ = handler.handle_payload(b"{}", {"X-GitHub-Event": "issues"})
    assert status == 400

    # Invalid signature
    success, status, msg, _ = handler.handle_payload(
        b"{}",
        {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "1", "X-Hub-Signature-256": "sha256=wrong"}
    )
    assert status == 401

    # Malformed JSON
    good_sig = _sign(b"not json", "sec")
    success, status, msg, _ = handler.handle_payload(
        b"not json",
        {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "1", "X-Hub-Signature-256": good_sig}
    )
    assert status == 400

    # Path traversal in repository
    bad_repo = json.dumps({"action": "opened", "repository": {"name": "../../evil"}, "issue": {"title": "X", "body": "Y"}}).encode("utf-8")
    bad_sig = _sign(bad_repo, "sec")
    success, status, msg, _ = handler.handle_payload(
        bad_repo,
        {"X-GitHub-Event": "issues", "X-GitHub-Delivery": "2", "X-Hub-Signature-256": bad_sig}
    )
    assert status == 422
    assert "directory traversal" in msg


def test_direct_api_ingestion_success_and_auth():
    api_key = "secure-direct-api-key-999"
    handler = DirectWorkItemIngestionHandler(api_key=api_key)

    payload = {
        "repository": "django/django",
        "issue_id": "TICKET-1234",
        "title": "QuerySet prefetch crash with null foreign key",
        "problem_statement": "Full description of Django prefetch defect.",
        "reproduction_test": "tests/prefetch_related/tests.py",
        "labels": ["orm", "regression"],
    }
    payload_bytes = json.dumps(payload).encode("utf-8")

    # 1. Unauthorized request
    ok, code, msg, _ = handler.handle_request(payload_bytes, {})
    assert ok is False
    assert code == 401

    # 2. Wrong Bearer token
    ok, code, msg, _ = handler.handle_request(payload_bytes, {"Authorization": "Bearer wrong-key"})
    assert ok is False
    assert code == 401

    # 3. Valid Bearer token
    ok, code, msg, item = handler.handle_request(payload_bytes, {"Authorization": f"Bearer {api_key}"})
    assert ok is True
    assert code == 200
    assert item is not None
    assert item.repository == "django/django"
    assert item.issue_id == "TICKET-1234"

    # 4. Valid X-API-Key header alternative
    ok, code, msg, item = handler.handle_request(payload_bytes, {"X-API-Key": api_key})
    assert ok is True
    assert code == 200
    assert item is not None

    # Conversion to Problem
    problem = item.to_problem()
    assert problem.instance_id == "TICKET-1234"
    assert problem.repo == "django/django"
    assert problem.fail_to_pass == ["tests/prefetch_related/tests.py"]


def test_direct_api_idempotency_and_limits():
    api_key = "key-lim"
    tracker = IngestionIdempotencyTracker()
    handler = DirectWorkItemIngestionHandler(
        api_key=api_key,
        idempotency_tracker=tracker,
        max_payload_bytes=100,  # Tiny limit for test
    )

    headers = {"Authorization": f"Bearer {api_key}", "Idempotency-Key": "idemp-001"}

    # 1. Payload size exceeded
    oversized = b"x" * 150
    ok, code, msg, _ = handler.handle_request(oversized, headers)
    assert ok is False
    assert code == 413
    assert "exceeds maximum allowed size" in msg

    # 2. Normal payload with idempotency key
    small_payload = json.dumps({"repository": "repo/test", "problem_statement": "Crash"}).encode("utf-8")
    ok, code, msg, item = handler.handle_request(small_payload, headers)
    assert ok is True
    assert code == 200
    assert item is not None

    # 3. Duplicate submission with same Idempotency-Key
    ok, code, msg, repeat_item = handler.handle_request(small_payload, headers)
    assert ok is True
    assert code == 200
    assert repeat_item is None
    assert "already processed" in msg


def test_direct_api_validation_errors():
    handler = DirectWorkItemIngestionHandler(require_auth=False)

    # Missing repository
    payload = json.dumps({"problem_statement": "Crash"}).encode("utf-8")
    ok, code, msg, _ = handler.handle_request(payload, {})
    assert ok is False
    assert code == 422
    assert "'repository' must not be empty" in msg

    # Directory traversal in repository
    payload = json.dumps({"repository": "../secret/repo", "problem_statement": "Crash"}).encode("utf-8")
    ok, code, msg, _ = handler.handle_request(payload, {})
    assert ok is False
    assert code == 422
    assert "directory traversal" in msg

    # Missing description and title
    payload = json.dumps({"repository": "owner/repo"}).encode("utf-8")
    ok, code, msg, _ = handler.handle_request(payload, {})
    assert ok is False
    assert code == 422
    assert "must provide a non-empty title or problem statement" in msg
