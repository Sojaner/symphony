---
description: Start Symphony orchestration through the mandatory bootstrap — profile verification, project confirmation, fit assessment — before any project work
argument-hint: <project description>
---

Invoke the symphony skill now, before any other action, answer, or clarifying question.

Then execute its bootstrap strictly in order, completing each step before starting the next, exactly as the skill specifies:

1. **Verify the orchestrator.** Determine the model and effort this task is actually running on — the user provides nothing in the prompt. The effort must be `medium` or `high`; low-effort roots are rejected. When the runtime profile is unsuitable or unverifiable, interact with the user per the skill's gate and questionnaire — never continue on a guess, and never treat a remembered or "previously selected" profile as evidence.
2. **Confirm the project.** Location (default assumption: the current working directory), outcome, and acceptance criteria — one questionnaire round, no file reads before confirmation.
3. **Assess the fit.** Size the project with a shallow scan; recommend direct execution when orchestration overhead would exceed its savings, and let the user decide.
4. Only after all three steps pass: bootstrap the conductor and run the project per the skill.

Project: $ARGUMENTS
