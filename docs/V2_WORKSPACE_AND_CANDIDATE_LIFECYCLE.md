# V2 Workspace and Candidate Lifecycle

V2 never resets or cleans a caller checkout. `WorkspaceManager.create` copies an explicit source directory into a unique owned path and writes a marker. `resolve_file` rejects absolute/escaping paths. `destroy` verifies the marker before removal.

A candidate is created from a parent workspace, so a residual repair starts from the tested parent source—not from V1’s original source plus a remembered replacement string. The ledger preserves rejected and accepted attempts. Current implementation is local filesystem/JSONL; durable database jobs are deferred until the repair path proves useful.
