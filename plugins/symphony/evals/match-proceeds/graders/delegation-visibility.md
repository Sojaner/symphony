---
type: regex
pattern: '(?s)(?=.*Delegating:\s*symphony_assessor\s+—\s+[^\n]+\s+—\s+(?:claude-opus-5|opus\s+5)/high\s+—\s+[^\n]*read-only[^\n]*)(?=.*Completed:\s*symphony_assessor\s+—\s+planned\s+—\s+tokens\s+not exposed by host\s+—\s+duration\s+not exposed by host)(?=.*Delegating:\s*symphony_lead\s+—\s+[^\n]+\s+—\s+(?:claude-sonnet-5|sonnet\s+5)/medium\s+—\s+[^\n]+)(?=.*Completed:\s*symphony_lead\s+—\s+planned\s+—\s+tokens\s+not exposed by host\s+—\s+duration\s+not exposed by host)(?=.*SYMPHONY_MODE:\s*medium)'
flags: i
target: last_message
---

One coherent planned dry-run report must bind strongest/high read-only assessment, separate balanced/medium execution, medium mode, and complete visibility fields.
