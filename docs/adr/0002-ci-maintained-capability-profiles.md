# Capability profiles are built by CI, not discovered at runtime

The 1.0.0 design promised runtime capability resolution: live provider capabilities, then a cached snapshot, then a shipped fallback, refreshed every 24 hours. No component could perform that refresh. Hooks get no model inventory from either host, neither CLI exposes a way to list models, and the thin-root boundary forbids the root from doing capability research, so every route resolved through the shipped fallback while the persisted snapshot field stayed empty. From 1.1.0 the tier-to-model mapping is a set of profiles built and verified by a scheduled CI workflow and shipped with the release; the runtime only selects which profile applies.

## Consequences

The persisted `capabilities` field is removed rather than left unwritten. On Claude Code it was worse than dead: the required model is compared against the model token parsed out of a packaged agent filename, so a snapshot naming any token without a matching agent file would block every lead spawn permanently, and the bad value was persisted into the recorded route so it survived retries. Deleting the field closes that hazard; a CI-generated agent-file set keeps the coupling satisfiable by construction.

Runtime still probes entitlement, but only to choose among pre-shipped profiles, never to learn model names. That probe reads host-private files whose schemas neither vendor documents, so it is treated as a best-effort hint with a lowest-entitlement fallback, and it never reads credential files.

The refresh keeps the *published* map honest. It does not, and cannot, push anything to an installed copy. Neither host offers a marketplace entry that floats over the latest release tag: an entry pins a branch or tag by `ref`, a commit by `sha`, or a semver range only on an `npm` source. Auto-update for a third-party marketplace defaults to off on Claude Code, so a release reaches a user when they ask for it and not before. The maintainer's own Codex install sitting at 1.0.0 while the tree read 1.0.1 is what proved this, and it is why the workflow's job is described as keeping the map from rotting rather than as delivering anything.

The marketplace entry's `version` is therefore not a pin and never was. It is ignored at install time for every source type, because the plugin manifest wins. It is kept in sync anyway, because `claude plugin tag` refuses to tag a release when the two disagree.
