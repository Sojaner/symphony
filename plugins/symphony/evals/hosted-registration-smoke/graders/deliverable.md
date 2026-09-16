---
type: regex
pattern: '(?=[\s\S]*JSON array)(?=[\s\S]*duplicate (?:headers?|column names?)[^\r\n]*(?:fail|reject|error))(?=[\s\S]*(?:field[- ]count|row[- ]width)[^\r\n]*(?:fail|reject|error))(?=[\s\S]*(?:\bvalid\b[^\r\n]*(?:produce|convert|conversion|output)|(?:produce|convert|conversion|output)[^\r\n]*\bvalid\b|\bpass(?:es)? validation[ \t]+(?:is|are)[ \t]+included[ \t]+in[ \t]+(?:the[ \t]+)?output(?:[ \t]+array)?\b))[\s\S]*'
flags: i
target: last_message
---

Require JSON-array output and independently observable valid, duplicate-header, and field-count acceptance outcomes without prescribing their prose layout. This is integration evidence for the lifecycle smoke, not full prose conformance for every contract detail.
