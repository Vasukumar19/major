# Scalability and Production Audit

## Current position

The present implementation is single-process, checkout-mutating, and artifact-file based. It has no user identity, job queue, database, cancellation, lease, durable state, tenant partition, or worker pool. It cannot safely support concurrent repair jobs against a shared repository clone because both the orchestrator and SWE-bench adapter force-clean checkouts.

## Minimum viable production architecture

```
API / webhook -> authentication + intake validation -> PostgreSQL jobs/events
                                               -> queue -> worker lease
                                                          -> isolated worktree/container
                                                          -> model gateway
                                                          -> object artifact store
                                               <- status/cancel/audit API
```

* PostgreSQL: jobs, attempts, immutable inputs, transitions, budgets, user/tenant ownership and audit index.
* Object storage: patches, compressed logs, source manifests and benchmark artifacts; database retains pointers/digests.
* Queue: start with a durable database-backed queue if load is modest; introduce Redis/managed queue only when worker throughput/latency needs it.
* Worker: a job lease, cancellation signal, hard wall-time/resource limits, and one ephemeral workspace per attempt.
* Model gateway: provider keys isolated from workers where possible; tenant-aware quotas and redaction policy.

## Scaling path

1. Make local single-user isolation/recovery correct.
2. Add durable job/event state and a single worker.
3. Add bounded parallel workers with base-repository cache locks and capacity limits.
4. Only then evaluate remote Docker/Kubernetes and high-throughput inference serving.

## Security controls

Retain GitHub HMAC verification and work-item path validation. Add SSRF/repository URL allowlists, per-job filesystem and network policy, secret redaction, no host Docker socket exposed to untrusted patch workloads, artifact access authorization, signed webhook replay protection, and immutable audit logs. Treat repository code and LLM output as untrusted.
