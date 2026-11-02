# V2 Execution Flow

1. Read explicit runtime configuration and probe requested services.
2. Copy a supplied base checkout into an owned root workspace.
3. Create every repair candidate as a child of its selected parent workspace.
4. Apply bounded changed contents only through workspace containment checks.
5. Record candidate identity, parent, digests, rationale and observations to JSONL.
6. Run selected tests only through an explicitly configured isolated executor.
7. Accept a refinement parent only with F2P progress and no observed regression; otherwise reject it.
8. Use the official SWE-bench harness as final acceptance.
