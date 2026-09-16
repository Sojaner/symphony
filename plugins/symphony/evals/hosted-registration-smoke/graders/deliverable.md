---
type: regex
pattern: '(?=[\s\S]*UTF-8)(?=[\s\S]*JSON array)(?=[\s\S]*duplicate (?:headers?|column names?))(?=[\s\S]*(?:field[- ]count|row[- ]width))[\s\S]*(?:Example|Case)[ \t]*1\b[\s\S]*(?:Example|Case)[ \t]*2\b[\s\S]*(?:Example|Case)[ \t]*3\b'
flags: i
target: last_message
---

Require UTF-8 input, JSON-array output, both validation rules, and three numbered acceptance examples.
