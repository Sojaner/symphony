---
type: regex
pattern: 'Completed:[* \t]*(?:\r?\n[ \t\r\n]*-[ \t]*)?([a-f0-9]+)/(?:symphony_)?assessor[ \t]+—[^\r\n]+—[ \t]*tokens[ \t]+(?:[0-9]+|not exposed by host)[ \t]+—[ \t]*duration[ \t]+(?:[0-9]+(?:\.[0-9]+)?(?:ms|s)?|not exposed by host)[\s\S]*(?:Completed:[ \t]*|\n[ \t]*-[ \t]*)(?!\1/)[a-f0-9]+/(?:symphony_)?lead[ \t]+—[^\r\n]+—[ \t]*tokens[ \t]+(?:[0-9]+|not exposed by host)[ \t]+—[ \t]*duration[ \t]+(?:[0-9]+(?:\.[0-9]+)?(?:ms|s)?|not exposed by host)'
flags: i
target: last_message
---

Require assessor and lead completion records with distinct reported ids, status, and numeric or explicitly unavailable tokens and duration. Accept either individually prefixed `Completed:` records or bullets under a `Completed:` heading. This checks final-report disclosure, not persisted role identity or the truth of reported usage; terminal child statistics and the accepted run-completion receipt provide the lifecycle evidence for the two distinct persisted roles.
