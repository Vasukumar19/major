"""Explicit runtime configuration for local inference and container execution.

Nothing here starts a daemon or guesses an installation directory. Services are
provided by configuration/environment and probed only when a caller asks.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass


@dataclass(frozen=True)
class ServiceProbe:
    name: str
    configured: bool
    ready: bool
    detail: str


@dataclass(frozen=True)
class RuntimeEnvironment:
    ollama_url: str | None = None
    docker_command: str | None = None
    ollama_timeout_seconds: int = 120

    @classmethod
    def from_environment(cls) -> "RuntimeEnvironment":
        raw_timeout = os.getenv("PATCHFORGE_OLLAMA_TIMEOUT", os.getenv("OLLAMA_TIMEOUT", "120"))
        try:
            timeout = max(1, int(raw_timeout))
        except ValueError:
            timeout = 120
        return cls(
            ollama_url=os.getenv("PATCHFORGE_OLLAMA_URL") or os.getenv("OLLAMA_HOST") or None,
            docker_command=os.getenv("PATCHFORGE_DOCKER_COMMAND") or None,
            ollama_timeout_seconds=timeout,
        )

    def resolve_docker_command(self) -> str | None:
        if self.docker_command:
            return self.docker_command
        return shutil.which("docker")

    def probe_ollama(self) -> ServiceProbe:
        if not self.ollama_url:
            return ServiceProbe("ollama", False, False, "Set PATCHFORGE_OLLAMA_URL to an explicitly configured endpoint.")
        url = self.ollama_url.rstrip("/") + "/api/tags"
        try:
            with urllib.request.urlopen(url, timeout=self.ollama_timeout_seconds) as response:
                if 200 <= response.status < 300:
                    return ServiceProbe("ollama", True, True, f"Ready at {self.ollama_url}")
                return ServiceProbe("ollama", True, False, f"HTTP {response.status} from configured endpoint")
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            return ServiceProbe("ollama", True, False, f"Configured endpoint unavailable: {exc}")

    def probe_docker(self) -> ServiceProbe:
        command = self.resolve_docker_command()
        if not command:
            return ServiceProbe("docker", False, False, "Set PATCHFORGE_DOCKER_COMMAND or put docker on PATH.")
        try:
            proc = subprocess.run([command, "info", "--format", "{{.ServerVersion}}"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.TimeoutExpired) as exc:
            return ServiceProbe("docker", True, False, f"Configured command is unavailable: {exc}")
        if proc.returncode == 0:
            return ServiceProbe("docker", True, True, f"Docker server {proc.stdout.strip()}")
        return ServiceProbe("docker", True, False, (proc.stderr or proc.stdout or "docker info failed").strip())
