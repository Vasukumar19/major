# V2 Data Contracts

`OwnedWorkspace`: immutable ID, root, parent workspace ID and base revision.

`Candidate`: ID, parent candidate ID, workspace ID, base revision, source/patch digests, changed files, rationale, decision status and test observations.

`TestObservation`: phase, selectors, passed/failed identities, output, exit code and timeout state.

`CandidateLedger`: append-only JSONL lifecycle events. A decided candidate cannot receive further observations. A parent must exist before a child is created.
