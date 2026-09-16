---
type: regex
pattern: '<!--\s*SYMPHONY_MODE:small\s*-->[\s\S]*<!--\s*SYMPHONY_RUN_COMPLETE:[a-f0-9]{16}\s*-->'
flags: i
target: last_message
---

Require small mode and a completion receipt in the hook's run-id format. The runtime hook validates the receipt value.
