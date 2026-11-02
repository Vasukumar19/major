from patchforge.runtime.environment import RuntimeEnvironment


def test_runtime_requires_explicit_ollama_endpoint(monkeypatch):
    monkeypatch.delenv("PATCHFORGE_OLLAMA_URL", raising=False)
    monkeypatch.delenv("OLLAMA_HOST", raising=False)
    env = RuntimeEnvironment.from_environment()
    probe = env.probe_ollama()
    assert not probe.configured
    assert "PATCHFORGE_OLLAMA_URL" in probe.detail


def test_runtime_uses_configured_docker_command_without_platform_guessing():
    env = RuntimeEnvironment(ollama_url=None, docker_command="configured-docker")
    assert env.resolve_docker_command() == "configured-docker"
