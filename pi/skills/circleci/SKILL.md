---
name: circleci
description: Check and watch CircleCI pipeline/workflow/job status for the current commit using CircleCI v2 API, then fetch failing job logs and fix issues.
---

# CircleCI Status + Watch (v2 API)

Use this skill when `circleci` CLI cannot show workflow/job run status.

## Requirements

- `curl`
- `jq`
- Optional token for private repos: `CIRCLECI_CLI_TOKEN`

## 1) Resolve commit + project slug

```bash
SHA=$(git rev-parse HEAD)
REMOTE=$(git remote get-url origin)
ORG_REPO=$(echo "$REMOTE" | sed -E 's#(git@github.com:|https://github.com/)##; s#\.git$##')
PROJECT_SLUG="gh/${ORG_REPO}"
```

## 2) Build auth header (optional)

```bash
TOKEN="${CIRCLECI_CLI_TOKEN}"
if [ -n "$TOKEN" ]; then
  AUTH=(-H "Circle-Token: $TOKEN")
else
  AUTH=()
fi
```

## 3) Find latest pipeline for current commit

```bash
PIPELINE_JSON=$(curl -sS "${AUTH[@]}" "https://circleci.com/api/v2/project/${PROJECT_SLUG}/pipeline" \
  | jq --arg sha "$SHA" '[.items[] | select(.vcs.revision == $sha)] | sort_by(.number) | reverse | .[0]')

PIPELINE_ID=$(echo "$PIPELINE_JSON" | jq -r '.id // empty')
[ -n "$PIPELINE_ID" ] || { echo "No pipeline found for $SHA"; exit 1; }

echo "$PIPELINE_JSON" | jq '{pipeline_id:.id, number, state, branch:.vcs.branch, revision:.vcs.revision, subject:.vcs.commit.subject}'
```

## 4) Get workflow(s) and job status

```bash
curl -sS "${AUTH[@]}" "https://circleci.com/api/v2/pipeline/${PIPELINE_ID}/workflow" \
| jq '.items[] | {workflow_id:.id, name, status, created_at, stopped_at}'

WORKFLOW_ID=$(curl -sS "${AUTH[@]}" "https://circleci.com/api/v2/pipeline/${PIPELINE_ID}/workflow" | jq -r '.items[0].id')
curl -sS "${AUTH[@]}" "https://circleci.com/api/v2/workflow/${WORKFLOW_ID}/job" \
| jq '.items[] | {name, job_number, status}'
```

## 5) Wait for the workflow to finish

Use this when jobs are still running. Resolve `WORKFLOW_ID` as in step 4, then start a `watch` in `poll`
mode with this probe, substituting the literal workflow id: watch commands run in their own shell, without
the variables set above. It exits 0 once the workflow is terminal (printing its status, which the `watch`
message shows), 1 while it runs, and 2 if the API call fails.

```bash
STATUS=$(curl -fsS ${CIRCLECI_CLI_TOKEN:+-H "Circle-Token: ${CIRCLECI_CLI_TOKEN}"} \
  "https://circleci.com/api/v2/workflow/<workflow-id>" | jq -er .status) || exit 2
echo "workflow_status=${STATUS}"
case "$STATUS" in
  success|failed|error|failing|canceled|unauthorized) exit 0 ;;
  *) exit 1 ;;
esac
```

Once it reports, list the jobs with step 4.

## 6) Failed-job log retrieval (for root cause)

- First list failed jobs:

```bash
curl -sS "${AUTH[@]}" "https://circleci.com/api/v2/workflow/${WORKFLOW_ID}/job" \
| jq -r '.items[] | select(.status=="failed") | .job_number'
```

- Then fetch step output URLs via v1.1 and print last lines from failed step logs:

```bash
JOB_NUMBER="<job-number>"
V1="https://circleci.com/api/v1.1/project/github/${ORG_REPO}/${JOB_NUMBER}"
FAILED_STEP_URL=$(curl -sS "${AUTH[@]}" "$V1" \
  | jq -r '.steps[] | select(any(.actions[]?; .status=="failed")) | .actions[] | select(.status=="failed") | .output_url' \
  | head -n1)

curl -sS "$FAILED_STEP_URL" | jq -r '.[].message' | tail -n 120
```

## 7) Fix flow

1. Identify failed jobs.
2. Pull failing step logs.
3. Make minimal code/config fix.
4. Run local validation where possible (e.g., `circleci config validate`).

## Notes

- Public projects may work without auth; private projects usually require a token.
- A commit may have multiple pipelines (re-runs/retriggers). Use highest pipeline number as latest.
- Workflow status is the top-level CI signal.
