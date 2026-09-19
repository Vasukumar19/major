# PatchForge AI — Open Source Reuse & Attribution Record

**Date**: 2026-09-19  
**Compliance Standard**: MIT / Apache 2.0 Licensing  
**Auditor**: Principal Autonomous Engineer  

---

## 1. Third-Party Reuse Policy

Before incorporating code from external open-source projects, PatchForge requires:
1. Verification of compatible open-source license (MIT, Apache 2.0, BSD).
2. Inspection of upstream source code to identify exact files and components.
3. Preservation of original copyright notices and license text.
4. Clear documentation of modifications made and rationale for reuse.

---

## 2. Inspected Upstream Repositories

### A. mini-SWE-agent
- **Repository URL**: `https://github.com/SWE-agent/mini-swe-agent`
- **Commit / Version Inspected**: HEAD (`92f9a94`, 2025/2026)
- **License**: MIT License (Copyright (c) 2025 Kilian A. Lieret and Carlos E. Jimenez)
- **Components Inspected**:
  - `src/minisweagent/agents/default.py` (Simple agent loop with format error recovery and step limits)
  - `src/minisweagent/environments/docker.py` (Minimal Docker environment abstraction)
  - `src/minisweagent/models/` (Model call wrappers)
- **Reuse Assessment**:
  - Adopt mini-SWE-agent's clean, minimal agent loop principles: replace bloated 13-tool agent architecture with a streamlined (MODEL + ENVIRONMENT + ACTION) pattern.
  - Rationale: Eliminates tool oscillation and token bloat in multi-turn mode.

---

### B. Agentless
- **Repository URL**: `https://github.com/OpenAutoCoder/Agentless`
- **Commit / Version Inspected**: HEAD (`5895ae8`, 2024)
- **License**: MIT License (Copyright (c) 2024 OpenAutoCoder)
- **Components Inspected**:
  - `agentless/fl/localize.py` & `FL.py` (Hierarchical fault localization: file -> class -> function)
  - `agentless/repair/repair.py` (Single-turn repair prompt generation & validation)
  - `agentless/repair/rerank.py` (Patch validation & test-guided candidate reranking)
- **Reuse Assessment**:
  - PatchForge's baseline localization and repair structure directly adapts the proven Agentless 2-phase hierarchical pipeline.
  - Adopt Agentless's multi-location formatting for multi-site repair targets.

---

### C. Aider
- **Repository URL**: `https://github.com/Aider-AI/aider`
- **Commit / Version Inspected**: HEAD (`v0.74.x`, 2024/2026)
- **License**: Apache License 2.0
- **Components Inspected**:
  - `aider/repomap.py` (Tree-sitter / AST symbol extraction and pagerank-based context selection)
  - `aider/coders/editblock_coder.py` (SEARCH/REPLACE block parsing and fuzzy indentation matching)
- **Reuse Assessment**:
  - PatchForge's `RepoMap` (`patchforge/repository/map.py`) and `ApplyPatchTool` (`patchforge/tools/editor.py`) adapt Aider's surgical context and SEARCH/REPLACE block conventions.

---

### D. RepoGraph
- **Repository URL**: `https://github.com/ozyyshr/RepoGraph`
- **Commit / Version Inspected**: HEAD (`2024`)
- **License**: Apache License 2.0
- **Components Inspected**:
  - AST symbol relationship extraction and graph querying.
- **Reuse Assessment**:
  - Currently evaluated. Benchmark evidence shows local lexical + AST symbol navigation is currently sufficient for single-site and callers/callees. Full graph database integration is deferred until multi-site cross-file defects demonstrate the need.

---

### E. Moatless Tools
- **Repository URL**: `https://github.com/aorwall/moatless-tools`
- **Commit / Version Inspected**: HEAD (`2024`)
- **License**: MIT License (Copyright (c) 2024 Albert Örwall)
- **Components Inspected**:
  - `moatless/codeblocks/` (Hierarchical code block representation)
  - `moatless/actions/` (Grounded semantic code editing)
- **Reuse Assessment**:
  - Inspecting code block boundary tracking to improve multi-site `EditSite` verification.
