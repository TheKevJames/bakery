import asyncio
import json
import os
import pathlib
import subprocess
from collections.abc import Iterator

import pytest

from bakery.collectors import scout
from bakery.gateway import config
from bakery.gateway import core
from testing import fake_github
from testing import gateway as testing_gateway
from testing import harness

Reply = harness.Reply
OWNER = 'TheKevJames'
REPO = f'{OWNER}/thing'
TOKEN = 'test-token'


def git(path: pathlib.Path, *args: str) -> str:
    return subprocess.run(
        ('git', '-C', str(path), *args),
        capture_output=True,
        text=True,
        check=True,
    ).stdout


def task(*args: str) -> str:
    return subprocess.run(
        ('task', *args), capture_output=True, text=True, check=True
    ).stdout


def make_upstream(root: pathlib.Path) -> pathlib.Path:
    """A repo standing in for GitHub, and a checkout of it on a branch."""
    upstream = root / 'upstream'
    (upstream / 'vendor').mkdir(parents=True)
    (upstream / 'app.py').write_text(
        'def main():\n    pass  # TODO: handle errors\n\n\n'
        'def other():\n    # FIXME: slow\n    return 1\n'
    )
    (upstream / 'vendor' / 'lib.py').write_text('# TODO: theirs\n')
    git(upstream, 'init', '-q', '-b', 'main')
    git(upstream, 'add', '.')
    git(
        upstream,
        '-c', 'user.name=t', '-c', 'user.email=t@t',
        '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', 'init',
    )  # fmt: skip
    checkout = root / 'checkout'
    git(root, 'clone', '-q', str(upstream), str(checkout))
    git(checkout, 'switch', '-q', '-c', 'feature')
    return checkout


def run(run_id: int, conclusion: str, workflow: int = 1) -> dict[str, object]:
    return {
        'id': run_id,
        'workflow_id': workflow,
        'name': f'workflow-{workflow}',
        'path': f'.github/workflows/w{workflow}.yml',
        'conclusion': conclusion,
        'html_url': f'https://github.com/{REPO}/actions/runs/{run_id}',
        'created_at': f'2026-10-0{run_id % 9 + 1}T00:00:00Z',
    }


def issue(number: int, **extra: object) -> dict[str, object]:
    return {
        'number': number,
        'title': f'issue {number}',
        'html_url': f'https://github.com/{REPO}/issues/{number}',
        'user': {'login': 'someone', 'type': 'User'},
        'labels': [],
        'body': f'body {number}',
    } | extra


def serve_repo(fake: fake_github.FakeGitHub) -> None:
    base = f'/repos/{REPO}'
    runs = f'{base}/actions/runs?branch=main&status=completed&per_page=100'
    fake.routes[base] = {'default_branch': 'main'}
    fake.routes[runs] = {
        # Newest first: workflow 1 has failed twice since its last success;
        # workflow 2 passes, so its logs are scanned for warnings.
        'workflow_runs': [
            run(10, 'failure', workflow=3) | {'event': 'dynamic'},
            run(9, 'failure'),
            run(8, 'success', workflow=2),
            run(7, 'timed_out'),
            run(6, 'success'),
        ]
    }
    fake.routes[f'{base}/actions/runs/9/jobs'] = {
        'jobs': [
            {
                'name': 'test',
                'conclusion': 'failure',
                'steps': [
                    {'name': 'setup', 'conclusion': 'success'},
                    {'name': 'pytest', 'conclusion': 'failure'},
                ],
            }
        ]
    }
    fake.redirects[f'{base}/actions/runs/8/logs'] = '/blob/logs.zip'
    fake.routes['/blob/logs.zip'] = fake_github.logs_zip(
        {
            '1_test.txt': (
                '2026-10-01T00:00:00.1Z DeprecationWarning: old api\n'
                '2026-10-01T00:00:01.2Z DeprecationWarning: old api\n'
                '2026-10-01T00:00:02.3Z all good\n'
            )
        }
    )
    issues = f'{base}/issues?per_page=100&state=open'
    fake.routes[issues] = [
        issue(1),
        issue(2, pull_request={}, user={'login': 'renovate', 'type': 'Bot'}),
        issue(3, pull_request={}),
        issue(4, labels=[{'name': 'ready-for-human'}]),
    ]
    fake.next_pages[issues] = '/page2'
    fake.routes['/page2'] = [issue(5)]


def scout_toml(root: pathlib.Path, api: str, max_candidates: int = 25) -> str:
    return f"""
max_candidates = {max_candidates}
opt_out_label = "ready-for-human"
api = "{api}"

[[repo]]
name = "{REPO}"
path = "{root / 'checkout'}"
remote = "file://{root / 'upstream'}"
exclude = ["vendor/**"]

[[repo]]
name = "{OWNER}/gone"
path = "{root / 'missing'}"
"""


@pytest.fixture(name='github', scope='function')
def fixture_github(
    root: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[fake_github.FakeGitHub]:
    monkeypatch.setenv('BAKERY_TEST_GH', TOKEN)
    monkeypatch.setenv('TASK_FOLDER', str(root / 'tasks'))
    (root / 'tasks').mkdir()
    make_upstream(root)
    with fake_github.serve() as fake:
        serve_repo(fake)
        yield fake


def scout_claws(
    root: pathlib.Path, api: str, max_candidates: int = 25
) -> pathlib.Path:
    claws = testing_gateway.make_claws(
        root,
        {
            'scout': """
                secrets = ["BAKERY_FAKE_LLM_URL", "GH_TOKEN=BAKERY_TEST_GH"]
                [ask]
                policy = "assume"
                [policy]
                tools = ["task_list", "task_add"]
                [[job]]
                name = "daily"
                cron = "0 7 * * *"
                prompt = "Add today's candidates."
                collector = "scout"
            """
        },
        extensions={'scout': ['pi/claw-extensions/task.ts']},
    )
    (claws / 'scout' / scout.SETTINGS_NAME).write_text(
        scout_toml(root, api, max_candidates)
    )
    return claws


def candidates(text: str | None) -> list[dict[str, str]]:
    assert text is not None
    body = text.split('<candidates', 1)[1].split('\n', 2)[2]
    parsed: list[dict[str, str]] = json.loads(body.split('</candidates>')[0])
    return parsed


def test_collects_new_candidates_in_priority_order(
    root: pathlib.Path, github: fake_github.FakeGitHub
) -> None:
    claw = config.load(scout_claws(root, github.url)).claw('scout')
    # Already tracked, at another line: still the same lineage.
    blob = f'https://github.com/{REPO}/blob/main/app.py'
    fixme = scout.fingerprint('    # FIXME: slow')
    task('add', 'old', '--link', f'{blob}?todo={fixme}#L99')

    collection = scout.collect(claw)

    warning = scout.fingerprint('DeprecationWarning: old api')
    todo = scout.fingerprint('    pass  # TODO: handle errors')

    found = candidates(collection.text)
    assert [
        (c['kind'], c['link'].removeprefix('https://github.com/'))
        for c in found
    ] == [
        ('ci', f'{REPO}/actions/runs/7'),
        ('pr', f'{REPO}/issues/3'),
        ('issue', f'{REPO}/issues/1'),
        ('issue', f'{REPO}/issues/5'),
        ('warning', f'{REPO}/actions/workflows/w2.yml?warning={warning}'),
        ('todo', f'{REPO}/blob/main/app.py?todo={todo}#L2'),
    ]
    assert 'test: pytest' in found[0]['detail']
    assert 'def main():' in found[-1]['detail']
    assert collection.errors and f'{OWNER}/gone' in collection.errors[0]
    # Log downloads follow a redirect without leaking the token to it.
    assert ('/blob/logs.zip', None) in github.requests
    assert (f'/repos/{REPO}', f'Bearer {TOKEN}') in github.requests
    # The checkout itself is untouched; only the private ref moved.
    checkout = root / 'checkout'
    assert git(checkout, 'branch', '--show-current').strip() == 'feature'
    assert git(checkout, 'rev-parse', '--verify', 'refs/bakery/scout/main')


def test_caps_candidates_per_run(
    root: pathlib.Path, github: fake_github.FakeGitHub
) -> None:
    claw = config.load(scout_claws(root, github.url, 2)).claw('scout')

    text = scout.collect(claw).text

    assert text is not None
    assert '<candidates count="2" deferred="5">' in text
    assert [c['kind'] for c in candidates(text)] == ['ci', 'pr']


def test_scout_adds_tasks_then_finds_nothing_new(
    root: pathlib.Path,
    github: fake_github.FakeGitHub,
    fake_llm: harness.FakeLLM,
) -> None:
    claws = scout_claws(root, github.url, 1)
    ci_link = f'https://github.com/{REPO}/actions/runs/7'
    fake_llm.queue(
        Reply(
            tool='task_add',
            args={'summary': 'Fix CI', 'description': 'd', 'link': ci_link},
        ),
        Reply(text='added 1'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        first = await fake.wait(gateway.trigger('scout', None, None))
        assert first.status == 'settled', first.reason
        assert 'actions/runs/7' in fake_llm.transcript(0)
        # Everything left is still new, but one per run: the next run
        # hands over the PR. Drop the other sources to see "nothing new".
        github.routes[f'/repos/{REPO}/issues?per_page=100&state=open'] = []
        github.next_pages.clear()
        runs = (
            f'/repos/{REPO}/actions/runs'
            '?branch=main&status=completed&per_page=100'
        )
        github.routes[runs] = {
            'workflow_runs': [run(9, 'failure'), run(7, 'failure')]
        }
        (root / 'checkout').rename(root / 'gone-checkout')
        gateway.trigger('scout', None, None)
        while not any('nothing new' in n for n in fake.notices):
            await asyncio.sleep(0.05)

    testing_gateway.run_gateway(claws, fake, scenario)
    [added] = json.loads(task('list', '-p', 'all', '--json'))
    assert (added['summary'], added['link'], added['tag']) == (
        'Fix CI',
        ci_link,
        'Triage',
    )
    assert len(fake_llm.requests) == 2


def test_task_claims(
    root: pathlib.Path,
    fake_llm: harness.FakeLLM,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv('TASK_FOLDER', str(root / 'tasks'))
    (root / 'tasks').mkdir()
    task('add', 'mine')
    task('add', 'theirs', '--owner', 'kevin')
    claws = testing_gateway.make_claws(
        root,
        {'triage': '[policy]\ntools = ["task_set", "task_show"]\n'},
        extensions={'triage': ['pi/claw-extensions/task.ts']},
    )
    fake_llm.queue(
        Reply(
            tool='task_set',
            args={'id': 1, 'claim': True, 'tag': 'Bakery/build'},
        ),
        Reply(tool='task_set', args={'id': 2, 'claim': True}),
        Reply(tool='task_set', args={'id': 2, 'release': True}),
        Reply(
            tool='task_set',
            args={'id': 1, 'release': True, 'description_append': 'n'},
        ),
        Reply(text='done'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        done = await fake.wait(gateway.trigger('triage', None, 'go'))
        assert done.status == 'settled', done.reason

    testing_gateway.run_gateway(claws, fake, scenario)
    results = fake_llm.tool_results()
    assert '"owner": "triage"' in results[0]
    assert 'owned by kevin' in results[1]
    assert 'not claimed by triage' in results[2]
    final = json.loads(task('show', '1', '--json'))
    assert (final['owner'], final['tag'], final['description']) == (
        None,
        'Bakery/build',
        'n',
    )
    assert os.environ['TASK_FOLDER'] == str(root / 'tasks')


def test_repo_claws_load() -> None:
    claw = config.load(harness.REPO / 'claws').claw('scout')
    settings = scout.load_settings(claw.profile)
    assert claw.job('daily').collector == 'scout'
    assert {r.name for r in settings.repos} >= {f'{OWNER}/bakery'}
    assert f'{OWNER}/core' not in {r.name for r in settings.repos}
    profile = json.loads((claw.profile / 'settings.json').read_text())
    for path in profile['extensions'] + profile['prompts']:
        assert (claw.profile / path).exists(), path
