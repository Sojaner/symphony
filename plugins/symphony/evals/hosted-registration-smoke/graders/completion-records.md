---
type: regex
pattern: '^[ \t]*(?:-[ \t]*)?(?:\*\*)?(?:Completed:[ \t]*)?`?([a-f0-9]+)`?[ \t]*/[ \t]*`?(?:symphony_)?assessor`?[ \t]+—[^—\r\n]*[A-Za-z0-9][^—\r\n]*—[ \t]*tokens[ \t]+(?:[0-9]+|not exposed by host)[ \t]+—[ \t]*duration[ \t]+(?:[0-9]+(?:\.[0-9]+)?(?:ms|s)?|not exposed by host)(?:\*\*)?[ \t]*\r?$[\s\S]*?^[ \t]*(?:-[ \t]*)?(?:\*\*)?(?:Completed:[ \t]*)?`?(?!\1`?[ \t]*/)([a-f0-9]+)`?[ \t]*/[ \t]*`?(?:symphony_)?lead`?[ \t]+—[^—\r\n]*[A-Za-z0-9][^—\r\n]*—[ \t]*tokens[ \t]+(?:[0-9]+|not exposed by host)[ \t]+—[ \t]*duration[ \t]+(?:[0-9]+(?:\.[0-9]+)?(?:ms|s)?|not exposed by host)(?:\*\*)?[ \t]*\r?$'
flags: im
target: last_message
---

Require assessor and lead completion records with distinct reported ids, status, and numeric or explicitly unavailable tokens and duration. The literal `Completed:` label is optional when an anchored record line carries all of that evidence. This checks final-report disclosure, not persisted role identity or the truth of reported usage; terminal child statistics and the accepted run-completion receipt provide the lifecycle evidence for the two distinct persisted roles.
