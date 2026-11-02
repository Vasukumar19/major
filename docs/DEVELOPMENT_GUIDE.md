# PatchForge AI — Developer & Contributor Guide

This guide provides instructions for software engineers extending, debugging, and validating PatchForge AI.

---

## 1. Local Environment Setup

### Prerequisites
- Python 3.10+ (tested on Python 3.12, Windows 11 & WSL2/Ubuntu)
- Git 2.30+
- Ollama (running locally)
- Docker Engine 24.0+ (required for live evaluation runs)

### Installation
```bash
# Clone the repository
git clone http://github.com/Vasukumar19/major.git
cd major

# Create a virtual environment
python -m venv .venv
# On Linux / macOS / WSL:
source .venv/bin/activate
# On Windows PowerShell:
.venv\Scripts\Activate.ps1

# Install dependencies
pip install -r requirements.txt

# Create local configuration file
cp .env.example .env
```

---

## 2. Running & Writing Tests

PatchForge uses `pytest` for all testing. Tests must run completely offline without relying on network calls or live LLMs.

```bash
# Run the complete test suite
python -m pytest tests -q

# Run an individual test file
python -m pytest tests/test_v19_phase1_contract_gate.py -v

# Run a single test case
python -m pytest tests/test_structured_repair.py -k test_json_action_parsing -v
```

### Adding a New Test
1. Place new test files in `tests/test_<feature>.py`.
2. Mock all external dependencies (LLM APIs, Docker, file systems) using `unittest.mock`:
   ```python
   from unittest.mock import patch, MagicMock
   import pytest
   from patchforge.repair.ranking import RepairSiteRanker
   
   def test_ranking_determinism():
       ranker = RepairSiteRanker()
       # assertions...
   ```
3. Verify that your new test executes in under 2 seconds.

---

## 3. Extending Pipeline Components

### A. Adding a New Retrieval Signal to `HybridRepositoryRetriever`
1. Open `patchforge/retrieval/hybrid.py`.
2. Implement your scoring function:
   ```python
   def _compute_custom_signal(self, candidate: RetrievedCandidate) -> float:
       # compute normalized score between 0.0 and 1.0
       return 0.5
   ```
3. Add a corresponding weight in `PatchForgeConfig` or `HybridRepositoryRetriever.__init__`.
4. Add a test in `tests/test_hybrid_retriever.py`.

### B. Adding a New Repair Action Type
1. Define the action enum in `patchforge/repair/schema.py`:
   ```python
   class RepairUnitType(str, Enum):
       REPLACE_BLOCK = "REPLACE_BLOCK"
       INSERT_BEFORE = "INSERT_BEFORE"
       INSERT_AFTER = "INSERT_AFTER"
       WRAP_TRY_EXCEPT = "WRAP_TRY_EXCEPT"
       NEW_ACTION = "NEW_ACTION"  # <--- your new action
   ```
2. Update prompt instructions in `patchforge/repair/structured_repair.py`.
3. Implement AST/line mutation logic in `patchforge/repair/reconstructor.py`.
4. Add tests in `tests/test_repair_reconstruction.py` and `tests/test_patch_action_format.py`.

### C. Adding an External Integration
1. Subclass or implement an ingestion adapter in `patchforge/integrations/`.
2. Always validate paths using `work_item._validate_safe_path()` to prevent directory traversal.
3. Emit a normalized `PatchForgeWorkItem` instance.
4. Add verification tests in `tests/test_v19_phase5_external_integrations.py`.

---

## 4. Debugging & Failure Forensics

When an evaluation run fails or behaves unexpectedly:
1. **Inspect Task Artifacts**:
   Open `results/v19_orchestrator/<instance_id>.json`.
   Review:
   - `trajectory`: Every action taken, turn-by-turn.
   - `hypotheses`: Generated diagnosis options and confidence scores.
   - `patch`: The exact unified git diff generated.
   - `test_output`: Raw stdout/stderr and exit codes from pytest inside Docker.
2. **Reproduce Single-Task Locally**:
   Run only the failing instance:
   ```bash
   python scripts/run_v05_cohort.py --instances pallets__flask-4992 --mode v1 --model qwen2.5-coder:14b
   ```
3. **Inspect Static Syntax Output**:
   If a patch failed to compile, test it with:
   ```python
   import ast
   ast.parse(modified_source_code)
   ```

---

## 5. Coding Standards & Git Workflow

- **Python Style**: Strict Python 3.10+ typing (`x: str | None`), dataclasses, and explicit imports.
- **Documentation**: Preserve docstrings and comments on untouched functions.
- **Security**: Never commit API keys, `.env` files, or unvalidated file paths.
- **Non-Destructive Git**: Never run `git reset --hard`, `git checkout .`, or force-push.
- **Pre-Commit Verification**: Always run `python -m pytest tests -q` before staging or committing changes.
