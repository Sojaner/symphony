---
type: regex
pattern: 'Completed:[ \t]*([a-f0-9]+)/(?:symphony_)?assessor[ \t]+—[^\r\n]+—[ \t]*tokens[ \t]+(?:[0-9]+|not exposed by host)[ \t]+—[ \t]*duration[ \t]+(?:[0-9]+(?:ms|s)?|not exposed by host)[\s\S]*Completed:[ \t]*(?!\1/)[a-f0-9]+/(?:symphony_)?lead[ \t]+—[^\r\n]+—[ \t]*tokens[ \t]+(?:[0-9]+|not exposed by host)[ \t]+—[ \t]*duration[ \t]+(?:[0-9]+(?:ms|s)?|not exposed by host)'
flags: i
target: last_message
---

Require completion records for distinct assessor and lead ids, with status and exposed or explicitly unavailable usage.
