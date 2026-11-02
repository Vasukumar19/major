"""Direct REST API and JSON Ingestion Handler for PatchForge AI.

Supports direct submission of repair requests via REST API or CLI, with:
- Bearer token / API-key authentication against PATCHFORGE_API_KEY.
- Input validation and path traversal sanitization.
- Idempotency via Idempotency-Key header.
- Payload size and structure verification.
- Mapping to normalized PatchForgeWorkItem.
"""
from __future__ import annotations

import json
import logging
import os
import re
import uuid
from typing import Any, Dict, Optional, Set, Tuple

from patchforge.integrations.github import IngestionIdempotencyTracker
from patchforge.integrations.work_item import PatchForgeWorkItem

logger = logging.getLogger(__name__)

# Maximum payload size: 5 MB
MAX_PAYLOAD_BYTES = 5 * 1024 * 1024


class DirectWorkItemIngestionHandler:
    """Handles direct REST API / JSON submissions into PatchForgeWorkItem."""

    def __init__(
        self,
        api_key: Optional[str] = None,
        require_auth: bool = True,
        idempotency_tracker: Optional[IngestionIdempotencyTracker] = None,
        max_payload_bytes: int = MAX_PAYLOAD_BYTES,
    ):
        self.api_key = api_key or os.environ.get("PATCHFORGE_API_KEY", "")
        self.require_auth = require_auth
        self.tracker = idempotency_tracker or IngestionIdempotencyTracker()
        self.max_payload_bytes = max_payload_bytes

    def handle_request(
        self,
        payload_bytes: bytes,
        headers: Dict[str, str],
    ) -> Tuple[bool, int, str, Optional[PatchForgeWorkItem]]:
        """Validates authentication, headers, size, parses JSON, and builds a WorkItem.

        Returns (success, http_status_code, message, work_item_or_none).
        """
        # Case-insensitive header dictionary
        hdr = {k.lower(): v for k, v in headers.items()}

        # 1. Check Payload Size
        if len(payload_bytes) > self.max_payload_bytes:
            return False, 413, f"Payload exceeds maximum allowed size ({self.max_payload_bytes} bytes).", None

        # 2. Authentication Verification
        if self.require_auth:
            if not self.api_key:
                # If auth is required but server has no API key configured, reject safely
                logger.error("Authentication required but PATCHFORGE_API_KEY is not configured.")
                return False, 500, "Server configuration error: Authentication key not configured.", None

            auth_header = hdr.get("authorization", "").strip()
            x_api_key = hdr.get("x-api-key", "").strip()

            provided_key = ""
            if auth_header.lower().startswith("bearer "):
                provided_key = auth_header[7:].strip()
            elif x_api_key:
                provided_key = x_api_key

            if not provided_key or provided_key != self.api_key:
                logger.warning("Unauthorized direct ingestion request: invalid or missing API key.")
                return False, 401, "Unauthorized: Invalid or missing API key.", None

        # 3. Idempotency Check
        idempotency_key = hdr.get("idempotency-key") or hdr.get("x-idempotency-key") or ""
        idempotency_key = idempotency_key.strip()
        if idempotency_key and self.tracker.is_duplicate(idempotency_key):
            logger.info(f"Duplicate direct request with Idempotency-Key '{idempotency_key}' ignored.")
            return True, 200, f"Request with idempotency key '{idempotency_key}' already processed.", None

        # 4. JSON Deserialization
        try:
            data = json.loads(payload_bytes.decode("utf-8"))
        except Exception as e:
            return False, 400, f"Malformed JSON payload: {e}", None

        if not isinstance(data, dict):
            return False, 400, "Invalid JSON payload: root must be a JSON object.", None

        # 5. Extract and Validate Fields
        repository = str(data.get("repository", "")).strip()
        problem_statement = str(data.get("problem_statement", "")).strip()
        title = str(data.get("title", "")).strip()
        issue_id = str(data.get("issue_id", "")).strip()
        base_commit = str(data.get("base_commit", "")).strip()
        reproduction_test = str(data.get("reproduction_test", "")).strip()
        hints_text = str(data.get("hints_text", "")).strip()
        labels_raw = data.get("labels", [])
        metadata_raw = data.get("metadata", {})

        if not isinstance(labels_raw, list):
            labels_raw = [str(labels_raw)]
        labels = [str(l).strip() for l in labels_raw if str(l).strip()]

        if not isinstance(metadata_raw, dict):
            metadata_raw = {}

        # Path traversal guard
        if ".." in repository:
            return False, 422, "Validation error: 'repository' contains forbidden directory traversal characters ('..').", None

        # Generate event ID if not given
        source_event_id = idempotency_key or f"direct-{uuid.uuid4().hex[:12]}"

        work_item = PatchForgeWorkItem(
            source_application="direct_api",
            source_event_id=source_event_id,
            repository=repository,
            problem_statement=problem_statement,
            issue_id=issue_id,
            title=title,
            base_commit=base_commit,
            reproduction_test=reproduction_test,
            hints_text=hints_text,
            labels=labels,
            metadata=metadata_raw,
        )

        valid, err_msg = work_item.validate()
        if not valid:
            return False, 422, f"Unprocessable work item: {err_msg}", None

        # Record idempotency if key was supplied
        if idempotency_key:
            self.tracker.record_delivery(idempotency_key)

        return True, 200, "Work item accepted successfully.", work_item
