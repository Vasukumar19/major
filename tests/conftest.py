"""Root test configuration for PatchForge test suite."""
import os

# Ensure git commands executed in temporary directories during test fixtures
# do not trigger Git 2.35+ dubious ownership errors on Windows.
os.environ.setdefault("GIT_CONFIG_COUNT", "1")
os.environ.setdefault("GIT_CONFIG_KEY_0", "safe.directory")
os.environ.setdefault("GIT_CONFIG_VALUE_0", "*")
