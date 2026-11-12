# Releases are tagged on merge, by a deploy key per repository

The Claw account is a write collaborator, so it could push a version tag
and so publish a release of its own unmerged branch. Merging a version
change is now the release, and only the operator can merge one. On every
repository, from the repo template, a tag ruleset lets only the repo admin
and deploy keys create, move or delete tags. When the default branch has a
version with no tag, tools' `_tag.yml` pushes one with that repository's
write deploy key. Only a job in the `tag` environment can read that key, and
only the default branch can run one. The tag push then triggers publishing
(PyPI, Forge) and the GitHub Release. The publishing environments only admit
release tags, and releases are immutable.

## Considered Options

- **The operator tags by hand.** Rejected: the operator wants merging to be
  the whole release.
- **`GITHUB_TOKEN`.** It can't be a ruleset bypass actor. A token any branch's
  run gets would hand tag creation back to the account's CI anyway. Its
  pushes also trigger no workflows.
- **A GitHub App** as the bypass actor. Its tokens are short-lived, it can't
  push workflow changes, and it could act across repositories. Rejected for
  its blast radius: one leaked key writes to every repository it is
  installed on. A deploy key only reaches its own repository.
- **Tagging last**, after publishing from the default branch, so that tags
  are an output. Rejected: the operator kept the tag as the trigger.
- **A required reviewer on the publishing environments.** Rejected: it adds
  a click to every release, when the merge is already the operator's
  decision.

## Consequences

- **Deploy keys are added in the web UI** (`repo-template set-deploy-key`).
  A key added through the API is deleted along with the token that added
  it.
- **The `DeployKey` bypass trusts every deploy key on a repository.** The
  repo-settings workflow fails if any write key other than the one titled
  `tag` appears. It only reports, it never deletes.
- **A leaked key can still forge that repository's tags.** It could also tag
  a commit whose workflow it rewrote, and so publish. Leak paths are limited
  to reviewed code: the job holding the key runs no repository code, only
  first-party actions and git.
- **Every merge that changes a version is a release.** The claws may bump
  versions in any pull request.
- **Merging requires code owner review.** Renovate's GitHub App is exempt, so
  its automerges need no approval. Only Renovate may push to `renovate/**`,
  so an automerge carries only Renovate's commits. Approvals from Actions
  never count, so Actions may not approve pull requests at all.
