# Code Style
- Follow existing project conventions
- Use meaningful variable names
- When functions exceed ~50 lines, start looking for opportunities to refactor some logic into a new function
- When files exceed ~450 lines, start looking for opportunities to refactor some logic into a new file
- Only rename imports (using `as`) when required to solve naming collisions
- Before implementing a feature as a special case, ask: "is this actually the general rule applied to a new domain?" If yes, implement the general rule and remove the special case, even if it's more work
- Helper scripts should live in "bin/"
- Prevent bad data from entering the system, don't program defenses against that bad data in each function

## Comments
- Only add comments where they will be useful as additional context for future people reading the code
- Do not re-state what the code does or explain why it was added (use the git commit message for this)
- Explain complicated gotchas and non-obvious reasons why the code should not changed
- Assume your audience is experienced and high-level engineers
- Do not refer to previous versions, eg. never add a comment stating how the code was before your change
