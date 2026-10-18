import datetime
import itertools
import os
import pathlib
import subprocess

import pytest

from bakery.collectors import changes
from bakery.gateway import config
from bakery.gateway import core
from bakery.gateway import jobs
from bakery.gateway import ledger
from bakery.gateway import prompts
from testing import gateway as testing_gateway
from testing import harness

KEVIN = 'Kevin James'
RENOVATE = 'renovate[bot]'
# defaults.toml's dream, on the claw's (fake) model.
DREAM = """
[[job]]
name = "dream"
cron = "0 3 * * *"
prompt = "/dream"
collector = "changes"
skip_empty = false
"""


@pytest.fixture(name='repo', scope='function')
def fixture_repo(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> pathlib.Path:
    """The bakery checkout the claws run from."""
    repo = tmp_path / 'repo'
    repo.mkdir()
    monkeypatch.setenv('BAKERY_REPO', str(repo))
    subprocess.run(('git', 'init', '-q', str(repo)), check=True)
    return repo


@pytest.fixture(name='claw', scope='function')
def fixture_claw(tmp_path: pathlib.Path) -> config.Claw:
    """A claw with the repo's default dream, which reads the changes."""
    claws = testing_gateway.make_claws(tmp_path, {'scout': ''})
    return config.load(claws).claw('scout')


def commit(
    repo: pathlib.Path, author: str, message: str, path: str, days_ago: int
) -> str:
    (repo / path).parent.mkdir(parents=True, exist_ok=True)
    (repo / path).write_text(message)
    when = datetime.datetime.now(datetime.UTC) - datetime.timedelta(
        days=days_ago
    )
    env = os.environ | {
        'GIT_AUTHOR_NAME': author,
        'GIT_AUTHOR_EMAIL': 'a@example.com',
        'GIT_COMMITTER_NAME': author,
        'GIT_COMMITTER_EMAIL': 'a@example.com',
        'GIT_AUTHOR_DATE': when.isoformat(),
        'GIT_COMMITTER_DATE': when.isoformat(),
    }
    for args in (
        ('add', path),
        ('-c', 'commit.gpgsign=false', 'commit', '-q', '-m', message),
    ):
        subprocess.run(('git', '-C', str(repo), *args), check=True, env=env)
    return changes.head(repo)


def dreamed(claw: config.Claw, commit_: str, status: ledger.Status) -> None:
    runs = ledger.Ledger(ledger.default_path())
    try:
        run = runs.start(claw.name, 'scout/dream-x', 'cron:dream', 'dream')
        runs.finish(run, status)
        run = runs.start(
            claw.name, 'scout/dream-y', 'cron:dream', 'dream', commit_
        )
        runs.finish(run, status)
    finally:
        runs.close()


def test_feeds_recent_commits_without_an_earlier_dream(
    repo: pathlib.Path, claw: config.Claw
) -> None:
    commit(repo, KEVIN, 'feat: old\n\nlong ago', 'old.py', days_ago=20)
    commit(
        repo, KEVIN, 'feat(sandbox): any host\n\nWhy it changed.',
        'bakery/sandbox.py', days_ago=3,
    )  # fmt: skip
    commit(repo, RENOVATE, 'chore(deps): bump x', 'uv.lock', days_ago=2)

    text = changes.collect(claw).text

    assert text is not None
    assert text.startswith(f'<changes checkout="{repo}">\n')
    assert 'in the last 14 days (no earlier dream on record)' in text
    assert 'feat(sandbox): any host\n\nWhy it changed.\n\n' in text
    assert 'Files: bakery/sandbox.py\n</commit>' in text
    for absent in ('feat: old', 'chore(deps)'):
        assert absent not in text


def test_feeds_commits_since_the_last_settled_dream(
    repo: pathlib.Path, claw: config.Claw
) -> None:
    commit(repo, KEVIN, 'feat: first', 'a.py', days_ago=30)
    covered = commit(repo, KEVIN, 'feat: covered', 'b.py', days_ago=20)
    commit(repo, KEVIN, 'feat: second', 'c.py', days_ago=19)
    newest = commit(repo, KEVIN, 'fix: third', 'c.py', days_ago=1)
    dreamed(claw, covered, ledger.Status.settled)
    # A dream that did not settle never moves the base.
    dreamed(claw, newest, ledger.Status.parked)

    text = changes.collect(claw).text

    assert text is not None
    assert f'since {covered[:12]}, the HEAD your last dream covered' in text
    assert text.index('feat: second') < text.index('fix: third')
    for absent in ('feat: first', 'feat: covered'):
        assert absent not in text


def test_nothing_new_since_the_last_dream(
    repo: pathlib.Path, claw: config.Claw
) -> None:
    newest = commit(repo, KEVIN, 'feat: covered', 'a.py', days_ago=1)
    dreamed(claw, newest, ledger.Status.settled)

    assert changes.collect(claw).text is None


def user_prompt(fake_llm: harness.FakeLLM, index: int) -> str:
    messages = fake_llm.requests[index]['messages']
    assert isinstance(messages, list)
    content = next(m for m in messages if m['role'] == 'user')['content']
    assert isinstance(content, list)
    return ''.join(str(part['text']) for part in content)


def test_a_dream_records_what_it_covered_and_runs_without_changes(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counter = itertools.count()
    monkeypatch.setattr(jobs, 'stamp', lambda: str(next(counter)))
    fake_llm.queue(*[harness.Reply(text='NO_REPLY')] * 3)
    fake = testing_gateway.FakeChannel()
    # pi would shell-parse this, then drop it: /dream takes no arguments.
    words = 'it\'s "quoted",\n\n  and   spaced'

    async def scenario(gateway: core.Gateway) -> None:
        for job, prompt in (('dream', None), ('dream', None), (None, words)):
            prompt = None if prompt is None else f'/dream {prompt}'
            work_key = gateway.trigger('scout', job, prompt)
            assert (await fake.wait(work_key)).status == 'settled'

    testing_gateway.run_gateway(
        testing_gateway.make_claws(root, {'scout': DREAM}), fake, scenario
    )

    body = prompts.expand('/dream')
    # pi got the template's body (and maybe this repo's recent changes),
    # never `/dream` to expand itself; the second dream had nothing new.
    assert user_prompt(fake_llm, 0).startswith(body)
    assert user_prompt(fake_llm, 1) == body
    assert user_prompt(fake_llm, 2) == f'{body}\n\n{words}'
    runs = ledger.Ledger(ledger.default_path())
    try:
        rows = runs.db.execute('SELECT bakery_commit FROM runs').fetchall()
    finally:
        runs.close()
    assert [r['bakery_commit'] for r in rows] == [
        changes.head(harness.REPO)
    ] * 3


def test_a_base_rewritten_away_falls_back_to_recent_commits(
    repo: pathlib.Path, claw: config.Claw
) -> None:
    commit(repo, KEVIN, 'feat: recent', 'a.py', days_ago=1)
    dreamed(claw, 'f' * 40, ledger.Status.settled)

    text = changes.collect(claw).text

    assert text is not None
    assert 'in the last 14 days' in text and 'feat: recent' in text
