# Tool Use
- Use the bash tool's `timeout` parameter when useful
- Wait on anything slower than ~10s (CI, merges, deploys, long test runs) with the `watch` tool, not in bash
- Read targeted ranges of files using the read tool with `offset` and `limit`
- For multi-line file content or scripts, use the `write` tool (or a `bin/` helper), not `cat <<EOF` / `echo` with embedded quotes
- Use `cat -vet` instead of `cat -A`

## System Features
You have access to the following additional shell tools that will help you find and discover things:

```
ast-grep (command: sg)
difftastic (command: difft)
docker-tags
eb
entr
fd-find (command: fd)
gh
git-ci-status
gog
hyperfine
jira-cli (command: jira)
jq
prek
pypi-versions
renovate / renovate-config-validator
ripgrep (command: rg)
shellcheck
sqlite3
yq
```
