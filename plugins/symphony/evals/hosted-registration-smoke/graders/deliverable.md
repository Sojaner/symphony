---
type: regex
pattern: '(?=[\s\S]*JSON array)(?=[\s\S]*duplicate (?:headers?|column names?)[^\r\n]*(?:fail|reject|error))(?=[\s\S]*(?:(?:field[- ]count|row[- ]width|row[ -]arity)[^\r\n]*(?:fail|reject|error)|row has [0-9]+ fields, expected [0-9]+))(?=[\s\S]*(?:\bvalid\b[^\r\n]*(?:produce|convert|conversion|output)|(?:produce|convert|conversion|output)[^\r\n]*\bvalid\b|\bpass(?:es)? validation[ \t]+(?:is|are)[ \t]+included[ \t]+in[ \t]+(?:the[ \t]+)?output(?:[ \t]+array)?\b|\bhappy path\b(?![^→\r\n]*\b(?:not|never|fail(?:s|ed|ure)?|error|reject(?:s|ed|ion)?)\b)\)?[ \t]*:[ \t]*input[^→\r\n]*→[ \t]*returns?[ \t]+exactly[ \t]+\[(?![^\]\r\n]*\b(?:error|fail(?:s|ed|ure)?)\b)))[\s\S]*'
flags: i
target: last_message
---

Require JSON-array output and independently observable valid, duplicate-header, and field-count acceptance outcomes without prescribing their prose layout. This is integration evidence for the lifecycle smoke, not full prose conformance for every contract detail.
