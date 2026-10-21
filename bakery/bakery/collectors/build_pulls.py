"""
Finding build's pull requests: the claws' GitHub user's, by the task their
branch names, `kjames/bakery-<id>-<slug>` (see build).

Search finds them across repositories, but its index can miss one for a
while, so before a task has none, the pull requests of each repository
search found any in, or which the task's link names, are listed too.
"""

import dataclasses
import datetime
import re
from typing import Any

from task import api

from .. import github
from . import build

# The repository a task's link is in, eg. the issue it came from.
LINKED_REPO = re.compile(r'https://github\.com/([^/?#]+/[^/?#]+)')


@dataclasses.dataclass(frozen=True)
class Pull:
    repo: str  # owner/name
    data: dict[str, Any]  # GET /repos/<repo>/pulls/<number>

    @property
    def path(self) -> str:
        return f'repos/{self.repo}'

    @property
    def number(self) -> int:
        return int(self.data['number'])


def _ident(data: dict[str, Any]) -> int | None:
    """The task a pull request's branch names."""
    match = build.BRANCH.match(data['head']['ref'])
    return None if match is None else int(match[1])


@dataclasses.dataclass
class Pulls:
    """
    `author`'s pull requests updated since `since`, by the task their branch
    names; an open one wins over closed ones, then the latest updated.
    """

    gh: github.GitHub
    author: str
    since: datetime.date
    found: dict[int, Pull] = dataclasses.field(default_factory=dict)
    # Where search found any: where to look again for one it missed.
    repos: set[str] = dataclasses.field(default_factory=set)
    listed: set[str] = dataclasses.field(default_factory=set)

    def _better(self, ident: int, data: dict[str, Any]) -> bool:
        if ident not in self.found:
            return True
        return bool(
            self.found[ident].data['state'] != 'open'
            and data['state'] == 'open'
        )

    def search(self) -> None:
        query = f'is:pr author:{self.author} updated:>={self.since}'
        items = self.gh.get_all(
            'search/issues', {'q': query, 'sort': 'updated'}, key='items'
        )
        for item in items:
            repo = item['repository_url'].split('/repos/', 1)[1]
            self.repos.add(repo)
            data = self.gh.get(f'repos/{repo}/pulls/{item["number"]}')
            ident = _ident(data)
            if ident is not None and self._better(ident, data):
                self.found[ident] = Pull(repo, data)

    def _list(self, repo: str) -> None:
        """`repo`'s pull requests, without search, latest updated first."""
        self.listed.add(repo)
        pages = self.gh.pages(
            f'repos/{repo}/pulls',
            {'state': 'all', 'sort': 'updated', 'direction': 'desc'},
        )
        try:
            for page in pages:
                for data in page:
                    if self._listed(repo, data):
                        return
        except github.GitHubError as e:
            # A link to something which is not a repository (eg.
            # github.com/advisories/<id>) has no pull requests.
            if e.status == 404:
                return
            raise

    def _listed(self, repo: str, data: dict[str, Any]) -> bool:
        """Take in one of `repo`'s pull requests; whether it is too old."""
        updated = datetime.datetime.fromisoformat(data['updated_at'])
        if updated.date() < self.since:
            return True
        ident = _ident(data)
        if (
            ident is not None
            and data['user']['login'].lower() == self.author.lower()
            and self._better(ident, data)
        ):
            # Listed pull requests lack some fields, eg. mergeable.
            full = self.gh.get(f'repos/{repo}/pulls/{data["number"]}')
            self.found[ident] = Pull(repo, full)
        return False

    def get(self, task: api.Task) -> Pull | None:
        """
        `task`'s pull request; if search found none, after listing each
        repository it might be in (once a poll: search's index can miss one
        for a while).
        """
        assert task.ident is not None, 'listed from the store'
        if task.ident not in self.found:
            repos = self.repos.copy()
            match = LINKED_REPO.match(task.link or '')
            if match is not None:
                repos.add(match[1])
            for repo in sorted(repos - self.listed):
                self._list(repo)
        return self.found.get(task.ident)
