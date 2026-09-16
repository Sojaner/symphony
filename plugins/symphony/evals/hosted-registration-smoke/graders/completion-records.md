---
type: regex
pattern: '(?<![\s\S])^(?![\s\S]*(?:tokens|duration)[ \t]+not exposed by host)[\s\S]*?^[ \t]*(?:-[ \t]*)?(?:\*\*)?(?:Completed:[ \t]*)?`?([a-f0-9]+)`?[ \t]*/[ \t]*`?(?:symphony_)?assessor`?[ \t]+—[^—\r\n]*[A-Za-z0-9](?:[^—\r\n]*)(?:[ \t]+—[ \t]*tokens[ \t]+[0-9]+)?(?:[ \t]+—[ \t]*duration[ \t]+[0-9]+(?:\.[0-9]+)?(?:ms|s)?)?(?:\*\*)?[ \t]*\r?$[\s\S]*?^[ \t]*(?:-[ \t]*)?(?:\*\*)?(?:Completed:[ \t]*)?`?(?!\1`?[ \t]*/)([a-f0-9]+)`?[ \t]*/[ \t]*`?(?:symphony_)?lead`?[ \t]+—[^—\r\n]*[A-Za-z0-9](?:[^—\r\n]*)(?:[ \t]+—[ \t]*tokens[ \t]+[0-9]+)?(?:[ \t]+—[ \t]*duration[ \t]+[0-9]+(?:\.[0-9]+)?(?:ms|s)?)?(?:\*\*)?[ \t]*\r?$'
flags: im
target: last_message
---

Require assessor and lead completion records with distinct reported ids and status. Numeric token or duration segments are accepted when exposed; unavailable placeholders are rejected. The literal `Completed:` label is optional when an anchored record line carries the identity and status. This checks final-report disclosure, not persisted role identity or the truth of reported usage; terminal child statistics and the accepted run-completion receipt provide the lifecycle evidence for the two distinct persisted roles.
