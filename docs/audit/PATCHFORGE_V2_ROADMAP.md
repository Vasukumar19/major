# PatchForge V2 Roadmap

## Stage 1 — Critical correctness

**Changes:** eliminate shared-checkout cleanup; introduce owned per-attempt workspaces, containment at every write, typed errors and controlled test temp root. **Affected:** `pipeline/v1_orchestrator.py`, `integrations/swebench.py`, `verification/tester.py`, new workspace module. **Acceptance:** a dirty caller checkout remains byte-identical; parallel attempts cannot collide; failures preserve artifacts. **Tests:** unit path/symlink/absolute path tests; integration simultaneous same-repo attempts. **Rollback:** feature flag to existing runner only for trusted legacy evaluation, never production.

## Stage 2 — Repair quality

**Changes:** separate diagnosis, plan and mutation; preserve accepted candidates; typed residual observations; CST spike for existing action types. **Dependencies:** Stage 1 artifact/workspace identity. **Acceptance:** target near-miss replays show stable candidate lineage and no loss of previously passing F2P tests. **Risk:** CST migration behavior changes; shadow-run reconstruction first. **Benchmark:** 4 near-miss pilot, then cohort.

## Stage 3 — Verification

**Changes:** explicit test manifest, targeted F2P before regression suite, candidate ledger/rollback, environment-failure classifier. **Acceptance:** every result includes per-test outcomes and candidate acceptance rationale; regressions are never silently retained. **Tests:** failure parser, rollback, timeout, harness-missing-report. **Rollback:** retain official full-harness final gate.

## Stage 4 — Repository intelligence

**Changes:** telemetry and predeclared ablations for BM25/test/graph/temporal signals; connect real failure observations to retrieval. **Acceptance:** published localization and latency deltas; only beneficial signals become default. **Risk:** benchmark tuning; maintain a held-out or delayed confirmation cohort.

## Stage 5 — Model and inference

**Changes:** context/token/latency instrumentation; provider capability matrix; local-vs-cloud and model-size pilot. **Dependencies:** reproducible prompts/candidate ledgers. **Acceptance:** comparison records resolved/F2P/P2P/cost/time with equal settings. **Rollback:** current Ollama provider remains supported.

## Stage 6 — Production foundations

**Changes:** PostgreSQL job/events, artifact storage, queue/leases, cancellation, quotas, worker isolation, observability. **Acceptance:** process restart recovers a nonterminal job without duplicate mutation; tenant/artifact authorization tests. **Risk:** operational overhead; start one worker and database-backed queue.

## Stage 7 — External integrations

**Changes:** REST job API, GitHub PR/status delivery, then Jira/Linear/Sentry/GitLab based on validated demand. **Acceptance:** signed/idempotent intake and least-privilege credentials; no direct repository mutation from webhooks. **Rollback:** disable adapter without invalidating job records.
