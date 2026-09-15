---
type: regex
pattern: '(?=.*(?:claude-haiku-4-5-20251001|haiku\s+4\.5))(?=.*(?:claude-opus-5|opus\s+5))(?=.*high)(?=.*capabilit)(?=.*SYMPHONY_MODE:\s*(?:small|medium|large))(?=.*SYMPHONY_RUN_COMPLETE:[a-f0-9]+)'
flags: is
target: last_message
---

The dry-run report must include the actual root, strongest/high lead, capability routing, one mode marker, and exact run receipt.
