"""
Claw prompt templates (`pi/claw-prompts/<name>.md`), expanded by the gateway.

pi would expand `/name text` itself, but it shell-parses `text` into
arguments (consuming quotes, collapsing whitespace) and drops them unless the
template asks for them. A run's opening prompt often carries text after a
template that must arrive as written (a collector's input, the operator's
words), so the gateway expands these templates itself and appends the text
verbatim. Messages steered into a running unit are left to pi.
"""

import pathlib
import re

from .. import paths

COMMAND_RE = re.compile(r'/([A-Za-z0-9_-]+)(?:\s+(.*))?', re.DOTALL)
FRONTMATTER_RE = re.compile(r'---\n.*?\n---\n', re.DOTALL)


def directory() -> pathlib.Path:
    return paths.repo() / 'pi' / 'claw-prompts'


def expand(text: str) -> str:
    """`text`, with a leading claw template replaced by its body."""
    match = COMMAND_RE.fullmatch(text)
    if match is None:
        return text
    template = directory() / f'{match[1]}.md'
    if not template.is_file():
        return text
    content = template.read_text()
    frontmatter = FRONTMATTER_RE.match(content)
    body = content[frontmatter.end() if frontmatter else 0 :].strip()
    return f'{body}\n\n{match[2]}' if match[2] else body
