# Workflow
- Read before write — understand context first. Re-read the exact target region immediately before an `edit`, especially for a file you edited earlier this session
- Minimal changes — don't refactor unrelated code
- Verify after changes — run linters, tests, and check output
- Ask before chosing a new approach - do not assume my preferences
- Do not install packages globally or configure my environment - ask me if you think you need to do this. You may make use of and install to local, git-controlled environments, such as running `uv sync` and using the associated venv
- If you ever run into issues where you think the environment is not set up properly, for example where you can't run tests, can't import a library from my codebase, can't run an interpreter, etc, ask me how to proceed
- Never remove `TODO` comments without asking me, unless you are solving that particular TODO
- Never say 'applied/implemented/done' unless you can immediately cite: (a) tool output confirming the edit, and (b) git diff (or re-read of the edited block)
- When a task can be solved with a built-in feature of the tool/framework, prefer that over custom workarounds
- Search docs before building regex/scripting solutions
- Do not fabricate theories or assume system state — verify with actual data before proposing root causes
- When you don't know something about a tool's API, read its documentation first rather than guessing and iterating
- When asked to fix X, apply the minimal targeted fix — do not broaden scope without asking
- Transient / flaky test failures should always be marked for investigation - do not interrupt your current work, but suggest it for immediate follow-up once you're done
- Update docs, TODOs, diagrams, changelogs, etc after changing anything they refer to
- For independent read-only investigation across multiple repos/services, fan out with parallel subagents before implementing
- Always explicitly state your assumptions

## File Access
- Never read files in gitignored folders unless explicitly necessary

## Docs-First Principle

Before implementing a workaround, building a regex hack, or guessing at tool/framework API behavior:

1. **Search documentation first.** Use the context7 skill or read official docs to check whether a built-in feature already solves the problem.
2. **Verify assumptions.** Do not assume system state, API limits, or framework behavior - look it up or test it. If you cannot verify, say so explicitly rather than guessing.
3. **Prefer built-in features.** If a framework provides a purpose-built solution (eg. stage.truncate, lifecycle ignore_changes), always prefer it over custom workarounds.
4. **Admit uncertainty.** When you don't know something, say "I'm not sure — let me check" rather than confidently stating something that might be wrong.
