# Specific File/Application Types
## CSS and JavaScript
- Prefer CSS over JavaScript when both can effectively solve a rendering issue
- After every CSS file edit, re-read the modified block to verify the rules are actually present
- Before implementing any UI component (tooltip, badge, modal, dropdown, etc.), search for an existing instance of the same component type in the codebase and replicate its implementation exactly. Never reach for a browser native (e.g. title=, <details>) if a custom pattern already exists.

## Python
- Bare `python` is not installed, use `python3` or the uv venv
- Never use `pip` or `pip install` directly
- System tools should be managed with `pipx`
- Prefer `uv` for managing python projects
- Prefer modern APIs (such as `pathlib`) over deprecated/older alternatives (eg. `os`)
- Prefer typed locals over cast for solving upstream typehint issues
- Prefer importing modules instead of classes or functions, unless you are importing from `typing` or `collections.abc`
