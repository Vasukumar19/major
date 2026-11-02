# PatchForge AI — Configuration & Environment Guide

This document describes all configuration settings, environment variables, default values, and deployment requirements.

---

## 1. Environment Variables Overview

PatchForge uses `python-dotenv` to load environment variables from a `.env` file in the project root:

| Variable Name | Default Value | Description |
|:---|:---|:---|
| `OLLAMA_HOST` | `http://127.0.0.1:11434` | URL of the local or remote Ollama daemon. |
| `PATCHFORGE_MODEL` | `qwen2.5-coder:14b` | Default LLM model identifier. |
| `OLLAMA_TIMEOUT` | `120` | HTTP request timeout in seconds for LLM generation. |
| `DOCKER_HOST` | *(empty / platform default)* | Custom Docker daemon socket (e.g. `npipe:////./pipe/docker_engine`). |
| `SWEBENCH_DATASET` | `princeton-nlp/SWE-bench_Lite` | Dataset name or local JSON path for SWE-bench instances. |
| `SWEBENCH_HARNESS_TIMEOUT` | `300` | Subprocess timeout in seconds for containerized test runs. |
| `GITHUB_TOKEN` | *(optional)* | Personal access token for GitHub REST API rate limit expansion. |
| `WEBHOOK_SECRET` | *(optional)* | Secret key for verifying GitHub webhook HMAC-SHA256 signatures. |
| `LOG_LEVEL` | `INFO` | Console logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `TELEMETRY_ENABLED` | `true` | Whether to log detailed execution timings and token counters. |

---

## 2. Programmatic Configuration (`PatchForgeConfig`)

**Module**: `patchforge.core.config`

The core dataclass `PatchForgeConfig` encapsulates pipeline settings:

```python
from dataclasses import dataclass, field

@dataclass
class PatchForgeConfig:
    default_model: str = "qwen2.5-coder:14b"
    max_patch_attempts: int = 3
    max_task_seconds: int = 1500
    temperature: float = 0.0
    workspace: str = "."
    enable_contract_gate: bool = True
    enable_ast_reconstruction: bool = True
    harness_timeout: int = 300
    telemetry_enabled: bool = True
```

### Key Parameters:
- `max_patch_attempts`: Number of iterative repair attempts per task (default: `3`).
- `temperature`: Model temperature for code synthesis (default: `0.0` for deterministic outputs).
- `enable_contract_gate`: When enabled, validates hypotheses against API contracts and behavioral requirements before synthesis.
- `enable_ast_reconstruction`: Uses AST span matching for patch application; falls back to line-bounded diffs if disabled.

---

## 3. Ollama Setup & Model Requirements

PatchForge relies on [Ollama](https://ollama.ai/) for local code intelligence:

1. **Verify Ollama is installed**:
   ```bash
   ollama --version
   ```
2. **Start the Ollama daemon**:
   ```bash
   ollama serve
   ```
3. **Pull the recommended model**:
   ```bash
   ollama pull qwen2.5-coder:14b
   ```
   *(Alternative lightweight model: `qwen2.5-coder:7b`)*
4. **Test daemon connectivity**:
   ```bash
   curl http://127.0.0.1:11434/api/tags
   ```

---

## 4. Docker & WSL Configuration

The evaluation harness executes official SWE-bench evaluation containers:

- **Docker Version**: Docker Engine 24.0+ (Docker Desktop on Windows with WSL2 backend).
- **Socket Permissions**: Current user must have permissions to invoke `docker ps` and `docker run`.
- **WSL Configuration**: On Windows 11, Docker Desktop runs via the WSL2 `desktop-linux` distribution. Ensure the WSL daemon is running and has sufficient memory allocated (minimum 8 GB RAM recommended).

---

## 5. Security & Sensitive Values

> [!CAUTION]
> - Never commit `.env` or files containing `GITHUB_TOKEN` / `WEBHOOK_SECRET` to Git.
> - The repository's `.gitignore` explicitly excludes `.env`.
> - Always use `.env.example` as a template:

### `.env.example` Reference
```bash
# LLM Inference Configuration (Ollama)
OLLAMA_HOST=http://127.0.0.1:11434
PATCHFORGE_MODEL=qwen2.5-coder:14b
OLLAMA_TIMEOUT=120

# Docker & SWE-bench Configuration
SWEBENCH_DATASET=princeton-nlp/SWE-bench_Lite
SWEBENCH_HARNESS_TIMEOUT=300

# External Integration Credentials (Optional)
GITHUB_TOKEN=your_github_personal_access_token_here
WEBHOOK_SECRET=your_github_webhook_hmac_secret_here

# Telemetry & Logging
LOG_LEVEL=INFO
TELEMETRY_ENABLED=true
```
