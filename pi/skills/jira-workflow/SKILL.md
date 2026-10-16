---
name: jira-workflow
description: Jira (dialpad.atlassian.net) via the `jira` CLI. Use for ANY Jira task, including listing my work, viewing, creating, commenting on, transitioning, linking or closing tickets (e.g. DP-123456), and board/filter queries.
---

# Jira

- My outstanding work (do not modify this command):
  `jira issue list -pX -a$(jira me) -RUnresolved -q 'project IS NOT EMPTY AND project NOT IN (SEC, OUT)' --plain`
- New-ticket defaults: project DP, assigned to me, needing triage. New tickets land in "Needs Triage" automatically.
- Close: `jira issue move KEY Close -R "Done"` (or `-R "Won't Do"`, `-R "Duplicate"`, etc), optionally with `--comment`.
- Link types use their exact names: `Relates`, `Blocks`, `Duplicate`.
- Epics: `jira epic create` fails (customfield_12277), so use `jira issue create -tEpic`. `jira epic add` does not take `--no-input`.
- jira-cli converts Markdown bodies and comments to wiki markup. Put blank lines around headings and lists, then check the result with `jira issue view KEY --comments 1`.
- REST fallback (boards, filters, custom fields): `curl -u "$JIRA_AUTH_USER:$JIRA_API_TOKEN" https://dialpad.atlassian.net/rest/...`. For search use `/rest/api/3/search/jql`; v2 `/search` has been removed.
- My Atlassian accountId: 5abea4ef85bc794916000ad0
