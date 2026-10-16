---
name: confluence
description: Read, search and edit Dialpad Confluence (dialpad.atlassian.net/wiki) via the `confluence` CLI. Use when the user shares a Confluence URL or asks to search, read or write Confluence docs.
---

# Confluence (`confluence` CLI)

Run `confluence <cmd> --help` for flags. This file only covers conventions and gotchas.

- Page args take a numeric ID or a `/pages/<id>/` URL. `/display/` URLs do an exact-title lookup.
- Read: `confluence read ID --format markdown | cat`. Comments: `confluence comments ID --format markdown --all`. Space and parent: `confluence info ID`.
- Linked Jira keys and pages are only in the storage XML: `confluence edit ID -o /tmp/p.xml`, then grep for `ac:name="key"` and `ri:content-title`.
- Search is full-text. For CQL add `--cql`: `confluence search --cql 'space=ENG AND title ~ "x"'`.
- Pipe output through `| cat` to deal with ANSI codes. For JSON use the global `--json` (`--format json` is deprecated).
- Writes: use `--format markdown`, and always pass `--yes` to destructive commands. Creating a new inline comment returns 400, so use a footer comment or reply with `--parent`. `move` only works within one space; otherwise use `copy-tree --dry-run` first.
- Round-trip edit: `edit ID -o p.xml` → modify → `update ID --file p.xml --format storage`.
