# Tool Use
- Use the bash tool's `timeout` parameter when useful
- Read targeted ranges of files using the read tool with `offset` and `limit`
- For multi-line file content or scripts, use the `write` tool (or a `bin/` helper), not `cat <<EOF` / `echo` with embedded quotes
- Use `cat -vet` instead of `cat -A`

## System Features
You have access to the following additional shell tools that will help you find and discover things:

```
ast-grep (command: sg)
difftastic (command: difft)
eb
entr
fd-find (command: fd)
gh
gog
gron
grpcurl
hyperfine
jira-cli (command: jira)
jq
kubectl mtail
kubectl stern
miller (command: mlr)
ngrok
prek
ripgrep (command: rg)
shellcheck
sqlite3
yq
```
