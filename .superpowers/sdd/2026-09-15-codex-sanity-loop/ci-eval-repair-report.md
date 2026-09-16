# Hosted Claude eval repair

Date: 2026-09-16. Baseline: main, Symphony v0.16.0. Failed CI run: 35037854422.

## Evidence and cause

Inspected `/tmp/symphony-ci-eval.faHsKy/eval-results.json`, its HTML report, the aggregate result, and the v0.16 hook and skill contracts. The graph returned no matching test symbols and reported changed source metadata; exact source reads supplied the evidence.

- **Weak-root dry run:** the fixture used `SYMPHONY_CONTROL: start` but omitted `--dry-run` from `SYMPHONY_TASK`. The hook therefore persisted a real run and correctly required accepted assessment. The artifact records one Agent call and a final assessment relay instead of the expected planned report. The actual fixture now passes through the deterministic hook test and must persist `dry_run=true`, inject dry-run guidance, and complete without an assessor.
- **Registration smoke:** the tool grader counted three Agent attempts, while the result's child statistics record two children actually spawned and completed. Visible evidence includes a denied `Task` launch, a denied read of a background result file, two host-backgrounded children, and `error_max_turns` at the 16-turn limit. The initial assessor packet also lacked the run id, explicit Symphony assignment, and required assessment receipt. The fixture now permits the Agent/Task aliases and blocking result tools, passes the required child packet, handles host backgrounding, and separates lead registration from run completion. Its turn budget is 24, with the existing 300-second timeout retained.
- **Registration grading:** the former rubric accepted a missing-mode Stop error as proof that assessment was accepted. Source inspection disproved that inference: the mode check precedes the accepted-assessment check. The rubric now requires hook acknowledgment and rejects max-turn exhaustion. The exact two-Agent limit remains unchanged.
- **Mismatch refusal:** both uploaded reports show the final regex failure and zero project/agent tool calls, but neither retains this case's final response. Its exact model failure is therefore unknown. The prompt now explicitly invokes the Symphony skill and bases actual values on trusted runtime metadata instead of asserting an unverified effort. The grader requires the requested three-line refusal and rejects affirmative continuation, missing restart guidance, and a falsely matching actual model. This is a stricter behavioral assertion, not evidence that the unseen original response would now pass.

The smoke's judge evidence elides 108 middle messages. Those elisions cannot prove missing lifecycle acknowledgments; fresh hosted results remain necessary.

## Changes and retained checks

Changes are limited to eval prompts, two graders, deterministic test coverage, and this report. Runtime hooks, skills, model profiles, manifests, and CI threshold are unchanged. The threshold stays **0.9**. The dry-run case still forbids Agent, Write, and Edit; the mismatch case retains all six no-project-action checks; the smoke still requires exactly two Agent attempts, distinct roles, accepted assessment before execution, actual registration acknowledgment, and the requested deliverable.

## Verification

- Before the fixes, the actual dry-run fixture test failed with `True != False`; the new prompt contract test failed on missing host result tools. The JavaScript adversarial refusal checks failed because the old lookahead-only grader accepted text that explicitly proceeded with implementation.
- `python3 -m unittest discover -s plugins/symphony/tests -v`: **131 tests passed**, including all original 130 tests and one new declaration test.
- `python3 -m unittest discover -s plugins/symphony/tests -k graders_compile -v`: passed; all shipped grader regexes compile in Node, and positive/negative refusal examples are checked in JavaScript.
- `claude plugin validate ./plugins/symphony`: passed.
- `git diff --check`: passed.

Local Claude Code is 2.1.236; failed CI used 2.1.273. Local authentication was available. The CI command's `--trust-plugin` option is unsupported by the local version. After omitting only that unsupported option, the one eval invocation exited 1 with `` `plugin eval` is currently in early access `` and produced no results. No hosted pass is claimed, no credentials were changed, and no threshold was lowered. A fresh CI run is required to establish hosted success, especially mismatch refusal and visibility of lifecycle acknowledgments in the truncated judge trace.
