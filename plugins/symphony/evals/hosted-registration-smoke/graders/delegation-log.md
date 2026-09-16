---
type: regex
pattern: '^.*Delegation log:.*Delegating:[^\r\n]*assessor \[[^/\]]+/[^\]]+\].*Completed:[^\r\n]*assessor \[[^/\]]+/[^\]]+\].*Delegating:[^\r\n]*lead \[[^/\]]+/[^\]]+\].*Completed:[^\r\n]*lead \[[^/\]]+/[^\]]+\]'
flags: is
target: last_message
---

Require the self-contained final response to preserve the cumulative assessor and lead delegation history with provider-visible model/effort labels.
