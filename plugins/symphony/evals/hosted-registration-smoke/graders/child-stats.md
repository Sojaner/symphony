---
type: regex
pattern: '(?:^|\n)[ \t]*\{(?=[^\r\n]*"type"\s*:\s*"result")(?=[^\r\n]*"subtype"\s*:\s*"success")(?![^\r\n]*"parent_tool_use_id"\s*:\s*")[^\r\n]*"subagent_stats"\s*:\s*\{(?=[^\r\n]*"spawned"\s*:\s*2\s*[,}])(?=[^\r\n]*"completed"\s*:\s*2\s*[,}])(?=[^\r\n]*"spawned_by_subagents"\s*:\s*0\s*[,}])(?=[^\r\n]*"failed"\s*:\s*0\s*[,}])[^\r\n]*\}\s*$'
flags: i
target: trace
---

Require the terminal root result at the end of the JSON-lines trace to have subtype success and host statistics showing exactly two started and completed children, no child delegation, and no failed children. Earlier successful statistics, child results, and error_max_turns do not qualify. A guard-denied attempt starts no child and does not change these counts.
