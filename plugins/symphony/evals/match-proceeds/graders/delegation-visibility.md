---
type: regex
pattern: '^(?!.*SYMPHONY_MODE:\s*(?:small|large))(?!.*SYMPHONY_MODE:.*SYMPHONY_MODE:)(?!(?:.*Delegating:){3})(?!(?:.*Completed:){3})(?!.*Delegating:[^\n]*symphony_assessor[^\n]*\b(?:implement|edit|delegate)\b).*Delegating:[ \t]*symphony_assessor[ \t]+—[ \t]+[^\n]+[ \t]+—[ \t]+(?:claude-opus-5|opus[ \t]+5)/high[ \t]+—[ \t]+[^\n]*read-only[^\n]*\n.*Completed:[ \t]*symphony_assessor[ \t]+—[ \t]+planned[ \t]+—[ \t]+tokens[ \t]+not exposed by host[ \t]+—[ \t]+duration[ \t]+not exposed by host\s*\n.*Delegating:[ \t]*symphony_lead[ \t]+—[ \t]+[^\n]+[ \t]+—[ \t]+(?:claude-sonnet-5|sonnet[ \t]+5)/medium[ \t]+—[ \t]+[^\n]+\n.*Completed:[ \t]*symphony_lead[ \t]+—[ \t]+planned[ \t]+—[ \t]+tokens[ \t]+not exposed by host[ \t]+—[ \t]+duration[ \t]+not exposed by host\s*\n.*SYMPHONY_MODE:[ \t]*medium\b'
flags: is
target: last_message
---

Require the four planned records in assessment-before-execution order, exactly two delegations and completions, one medium mode marker, and no assessor implementation claim. This grades a planned report, not actual spawns or host receipt acceptance.
