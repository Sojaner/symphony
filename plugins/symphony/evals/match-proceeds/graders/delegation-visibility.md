---
type: regex
pattern: '(?s)Delegating:\s*symphony_assessor.*?Completed:\s*.*symphony_assessor.*?Delegating:\s*symphony_lead.*?Completed:\s*.*symphony_lead'
flags: i
target: last_message
---

The planned dry-run report must visibly separate assessor and execution-lead delegations and completions.
