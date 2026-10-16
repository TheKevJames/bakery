---
name: google-workspace
description: Read and write Google Docs, Sheets and Drive files via the `gog` CLI. Use when the user shares a docs.google.com or drive.google.com link, or asks to read, create or update Google Docs, Sheets or Drive files.
---

# Google Workspace (`gog`)

Always use `gog`. Never web_fetch or curl Google URLs, and never write to `~/Library/CloudStorage`. Check `gog <group> <cmd> --help` for flags.

- Pass the bare ID (the part after `/d/`), not the URL.
- Read a Doc: `gog doc cat ID`. As Markdown: `gog doc export ID --format md --out /tmp/d.md`. (`gog doc get` is metadata only.)
- The Sheets API is disabled for our OAuth client, so read sheets through Drive: `gog drive download ID --format csv --out /tmp/s.csv`.
- Create from Markdown: `gog doc create "Title" --file x.md`, or `gog drive upload x.md --name "Title" --convert-to doc`.
- Replace a Doc's contents: `gog doc write ID --file x.md --markdown --replace`.
- Find files: `gog drive search "<query>"`, `gog drive ls`.
