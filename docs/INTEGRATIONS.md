# PatchForge AI — External Integrations & Ingestion Boundaries

This document details the external input boundaries, validation mechanisms, and adapters implemented in PatchForge AI (`patchforge/integrations/`).

---

## 1. Implemented Integrations Summary

PatchForge AI implements four concrete input boundaries:
1. **Normalized Work Item Ingestion** (`patchforge.integrations.work_item`)
2. **GitHub Webhook & REST API Adapter** (`patchforge.integrations.github`)
3. **Direct JSON Work Item Handler** (`patchforge.integrations.direct`)
4. **SWE-bench Benchmark Ingestion** (`patchforge.integrations.swebench`)

> [!IMPORTANT]
> **Server Runtime Boundary**: PatchForge provides production-tested **in-process ingestion handler classes** (`GitHubWebhookHandler`, `DirectWorkItemIngestionHandler`) rather than a standalone background web daemon. To run them as HTTP services, wire them into an ASGI/WSGI framework (e.g. FastAPI, Starlette, or Flask) or invoke them via CLI/scripts.
> 
> PatchForge **does not** maintain integrations for Jira, Linear, Sentry, GitLab, or PagerDuty. External systems must normalize their tickets into `PatchForgeWorkItem`.

---

## 2. Normalized Work Item (`PatchForgeWorkItem`)

**Module**: `patchforge.integrations.work_item`

All external inputs are parsed into a strongly typed, validated dataclass:

```python
@dataclass
class PatchForgeWorkItem:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    created_at: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
```

### Security & Path Containment
`PatchForgeWorkItem` enforces `_validate_safe_path(path: str) -> bool`:
- Rejects paths containing relative directory traversal tokens (`..`).
- Rejects absolute paths (`/etc/passwd` or `C:\Windows\System32`).
- Guarantees repository workspace boundaries cannot be breached.

---

## 3. GitHub Webhook & REST Integration (`GitHubWebhookHandler`)

**Module**: `patchforge.integrations.github`

### A. Webhook Signature Verification
`GitHubWebhookHandler.verify_signature(raw_payload: bytes, signature_header: str) -> bool`:
- Calculates HMAC-SHA256 using the configured secret key over the **raw binary request body**.
- Compares against `X-Hub-Signature-256: sha256=...` using constant-time `hmac.compare_digest`.
- Rejects mismatched, forged, or missing signatures.

### B. In-Memory Idempotency & Replay Protection
`IngestionIdempotencyTracker`:
- Tracks `X-GitHub-Delivery` UUIDs.
- Maintains an in-memory dictionary with timestamps:
  ```python
  tracker = IngestionIdempotencyTracker(max_entries=10000, ttl_seconds=86400)
  ```
- **Operational Limitation**: Idempotency tracking is currently **single-process and in-memory**. Process restarts reset the tracking table; multi-worker web deployments require an external distributed cache (e.g. Redis).

### C. GitHub Issues Payload Extraction
Handles `issues` and `issue_comment` event types:
- Extracts `issue.number`, `repository.full_name`, `issue.title`, and `issue.body`.
- Maps into a `PatchForgeWorkItem` with generated `instance_id = f"{repo_slug.replace('/', '__')}-{issue_num}"`.

### D. REST Fetching (`fetch_issue_and_create_work_item`)
Directly queries GitHub REST API (`https://api.github.com/repos/{owner}/{repo}/issues/{issue_number}`):
- Supports optional `GITHUB_TOKEN` for authenticated requests (5,000 req/hr limit).
- Returns normalized `PatchForgeWorkItem`.

---

## 4. Direct JSON Ingestion (`DirectWorkItemIngestionHandler`)

**Module**: `patchforge.integrations.direct`

Enables programmatic submission of work items from internal CI/CD pipelines, test runners, or developer scripts:

### Expected JSON Schema
```json
{
  "instance_id": "pallets__flask-4992",
  "repo": "pallets/flask",
  "base_commit": "c4583151b5c46369c7356d787ec347eb112c3f80",
  "problem_statement": "flask.blueprints raises ValueError on duplicate blueprint name registration...",
  "hints_text": "Check register_blueprint in app.py",
  "metadata": {
    "source": "ci_pipeline",
    "priority": "P1"
  }
}
```

### Handler API
```python
handler = DirectWorkItemIngestionHandler(workspace_root="./workspaces")
work_item = handler.ingest_json(json_payload_str)
```

---

## 5. SWE-bench Ingestion (`SWEBenchAdapter`)

**Module**: `patchforge.integrations.swebench`

Interfaces with Princeton's official SWE-bench evaluation benchmark:
- Loads official dataset tasks from Hugging Face (`princeton-nlp/SWE-bench_Lite`) or local cached JSON files.
- Extracts `FAIL_TO_PASS` and `PASS_TO_PASS` test lists.
- Sets up temporary task workspaces, runs the evaluation Docker container, and collects evaluation outputs.
