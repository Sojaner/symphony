# Capability profiles are built by CI, not discovered at runtime

The 1.0.0 design promised runtime capability resolution: live provider capabilities, then a cached snapshot, then a shipped fallback, refreshed every 24 hours. No component could perform that refresh. Hooks get no model inventory from either host, neither CLI exposes a way to list models, and the thin-root boundary forbids the root from doing capability research, so every route resolved through the shipped fallback while the persisted snapshot field stayed empty. From 1.1.0 the tier-to-model mapping is a set of profiles built and verified by a scheduled CI workflow and shipped with the release; the runtime only selects which profile applies.

## Consequences

The persisted `capabilities` field is removed rather than left unwritten. On Claude Code it was worse than dead: the required model is compared against the model token parsed out of a packaged agent filename, so a snapshot naming any token without a matching agent file would block every lead spawn permanently, and the bad value was persisted into the recorded route so it survived retries. Deleting the field closes that hazard; a CI-generated agent-file set keeps the coupling satisfiable by construction.

Runtime still probes entitlement, but only to choose among pre-shipped profiles, never to learn model names. That probe reads host-private files whose schemas neither vendor documents, so it is treated as a best-effort hint with a lowest-entitlement fallback, and it never reads credential files.
