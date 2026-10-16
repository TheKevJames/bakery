---
name: circleci
description: Inspect, wait on, and fetch failing-step logs for CircleCI pipelines (checks prefixed with `ci/circleci`). Not for GitHub Actions - use `gh run` for those.
---

# CircleCI (API v2 + v1.1 logs)

First confirm which CI failed: `gh pr checks` (or `gh api repos/{o}/{r}/commits/$SHA/check-runs`).
CircleCI links look like `https://circleci.com/gh/<org>/<repo>/<job-number>`; with a job number, skip to "Failed-step logs".

Auth: inline `${CIRCLECI_CLI_TOKEN:+-H "Circle-Token: ${CIRCLECI_CLI_TOKEN}"}` unquoted in each `curl`. Do not store
the header in a variable: word-splitting turns the token into a hostname, and curl prints it in its error.

## Pipeline → workflows → jobs

```bash
SHA=$(git rev-parse HEAD); BRANCH=$(git branch --show-current)
ORG_REPO=$(git remote get-url origin | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
API=https://circleci.com/api/v2
PID=$(curl -sS ${CIRCLECI_CLI_TOKEN:+-H "Circle-Token: ${CIRCLECI_CLI_TOKEN}"} "$API/project/gh/$ORG_REPO/pipeline?branch=$BRANCH" \
  | jq -r --arg sha "$SHA" '[.items[] | select(.vcs.revision == $sha)] | max_by(.number) | .id // empty')
curl -sS ${CIRCLECI_CLI_TOKEN:+-H "Circle-Token: ${CIRCLECI_CLI_TOKEN}"} "$API/pipeline/$PID/workflow" | jq -r '.items[] | "\(.id) \(.name) \(.status)"'
curl -sS ${CIRCLECI_CLI_TOKEN:+-H "Circle-Token: ${CIRCLECI_CLI_TOKEN}"} "$API/workflow/<workflow-id>/job" | jq -r '.items[] | "\(.job_number) \(.status) \(.name)"'
```

An empty `PID` means no CircleCI pipeline for this commit: re-check with `gh pr checks`, don't retry variants.

## Wait
`watch` poll probe (literal workflow id; watch shells don't inherit variables):

```bash
STATUS=$(curl -fsS ${CIRCLECI_CLI_TOKEN:+-H "Circle-Token: ${CIRCLECI_CLI_TOKEN}"} \
  "https://circleci.com/api/v2/workflow/<workflow-id>" | jq -er .status) || exit 2
echo "workflow_status=${STATUS}"
case "$STATUS" in success|failed|error|failing|canceled|unauthorized|on_hold) exit 0 ;; *) exit 1 ;; esac
```

## Failed-step logs (v1.1; output URLs are presigned, no auth)

```bash
V1="https://circleci.com/api/v1.1/project/github/${ORG_REPO}/<job-number>"
J=$(curl -sS ${CIRCLECI_CLI_TOKEN:+-H "Circle-Token: ${CIRCLECI_CLI_TOKEN}"} "$V1")
echo "$J" | jq -r '.steps[] | .name as $n | .actions[] | "\(.status)\t\($n)"'
for u in $(echo "$J" | jq -r '.steps[].actions[] | select(.status=="failed") | .output_url'); do
  curl -sS "$u" | jq -r '.[].message' | sed -E 's/\x1b\[[0-9;]*m//g' | tail -n 120
done
```

After editing `.circleci/config.yml`, run `circleci config validate`.
