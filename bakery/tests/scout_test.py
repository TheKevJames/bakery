import json
import pathlib
from collections.abc import Iterator

import pytest

from bakery.chat import render
from bakery.collectors import base as collectors_base
from bakery.collectors import scout
from bakery.collectors import scout_repos
from bakery.collectors import wontfix
from bakery.gateway import config
from bakery.gateway import control
from bakery.gateway import core
from testing import commands
from testing import fake_github
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks

OWNER = 'TheKevJames'
REPO = f'{OWNER}/thing'
COLLECTOR_TOKEN = 'collector-token'
MODEL_TOKEN = 'model-token'


def commit(path: pathlib.Path, message: str) -> None:
    commands.git(path, 'add', '.')
    commands.git(
        path,
        '-c', 'user.name=t', '-c', 'user.email=t@t',
        '-c', 'commit.gpgsign=false', 'commit', '-q', '-m', message,
    )  # fmt: skip


def make_upstream(root: pathlib.Path) -> pathlib.Path:
    """A repo standing in for GitHub's, under `remote`."""
    upstream = root / 'remote' / f'{REPO}.git'
    (upstream / 'vendor').mkdir(parents=True)
    (upstream / 'app.py').write_text(
        'def main():\n    pass  # TODO: handle errors\n\n\n'
        'def other():\n    # FIXME: slow\n    return 1\n'
    )
    (upstream / 'vendor' / 'lib.py').write_text('# TODO: theirs\n')
    commands.git(upstream, 'init', '-q', '-b', 'main')
    commit(upstream, 'init')
    # Workflow 4 changes after its run, so that run's warnings are stale.
    (upstream / '.github' / 'workflows').mkdir(parents=True)
    (upstream / '.github' / 'workflows' / 'w4.yml').write_text('fixed\n')
    commit(upstream, 'ci')
    return upstream


def run(
    run_id: int, conclusion: str, workflow: int = 1, head_sha: str = ''
) -> dict[str, object]:
    return {
        'head_sha': head_sha,
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


def serve_repo(fake: fake_github.FakeGitHub, initial: str) -> None:
    fake.routes['/user/repos?per_page=100&affiliation=collaborator'] = [
        {'full_name': name, 'name': name.split('/')[1], 'archived': False}
        for name in (REPO, f'{OWNER}/gone')
    ] + [{'full_name': f'{OWNER}/old', 'name': 'old', 'archived': True}]
    base = f'/repos/{REPO}'
    runs = f'{base}/actions/runs?branch=main&status=completed&per_page=100'
    fake.routes[base] = {'default_branch': 'main'}
    fake.routes[runs] = {
        # Newest first: workflow 1 has failed twice since its last success;
        # workflow 2 passes, so its logs are scanned for warnings. Workflow
        # 4 changed since its run, and workflow 5's commit is gone, so their
        # logs (unserved here) are not.
        'workflow_runs': [
            run(10, 'failure', workflow=3) | {'event': 'dynamic'},
            run(9, 'failure'),
            run(8, 'success', workflow=2, head_sha=initial),
            run(7, 'timed_out'),
            run(6, 'success'),
            run(5, 'success', workflow=4, head_sha=initial),
            run(4, 'success', workflow=5, head_sha='0' * 40),
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
    fake.authorized[f'{base}/actions/runs/8/logs'] = COLLECTOR_TOKEN
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
git_url = "{(root / 'remote').as_uri()}"

[[repo]]
name = "{REPO}"
exclude = ["vendor/**"]

[[repo]]
name = "{OWNER}/elsewhere"
exclude = ["x/**"]
"""


@pytest.fixture(name='github', scope='function')
def fixture_github(
    root: pathlib.Path, keychain: dict[str, str]
) -> Iterator[fake_github.FakeGitHub]:
    keychain[collectors_base.GITHUB_TOKEN] = COLLECTOR_TOKEN
    keychain['BAKERY_TEST_GH'] = MODEL_TOKEN
    testing_tasks.create(root / 'tasks')
    initial = commands.git(make_upstream(root), 'rev-parse', 'HEAD~').strip()
    with fake_github.serve() as fake:
        serve_repo(fake, initial)
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
                [[job]]
                name = "daily"
                cron = "0 7 * * *"
                prompt = "Add today's candidates."
                collector = "scout"
            """
        },
        extensions={'scout': ['pi/claw-extensions/task.ts']},
    )
    (claws / 'scout' / scout_repos.SETTINGS_NAME).write_text(
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
    testing_tasks.task('add', 'old', '--link', f'{blob}?todo={fixme}#L99')
    # Rejections stay tracked once done; other done tasks free their link.
    issues = f'https://github.com/{REPO}/issues'
    testing_tasks.task(
        'add',
        'no',
        '-n',
        'bakery',
        '--tag',
        'wontfix',
        '--link',
        f'{issues}/5',
    )
    testing_tasks.task(
        'add',
        'fixed',
        '-n',
        'bakery',
        '--tag',
        'build',
        '--link',
        f'{issues}/1',
    )
    wontfix.add('bakery:1', 'not ours')
    testing_tasks.task('bakery:1', 'done')
    testing_tasks.task('bakery:2', 'done')
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
        ('warning', f'{REPO}/actions/workflows/w2.yml?warning={warning}'),
        ('todo', f'{REPO}/blob/main/app.py?todo={todo}#L2'),
    ]
    assert 'test: pytest' in found[0]['detail']
    assert found[3]['detail'].startswith(
        f'From https://github.com/{REPO}/actions/runs/8 (commit '
    )
    assert 'def main():' in found[-1]['detail']
    [gone] = collection.errors
    assert f'{OWNER}/gone: reading the repo failed' in gone
    assert scout_repos.check(claw) == [
        f'scout: scout.toml overrides {OWNER}/elsewhere, which the claws'
        " cannot reach; it's ignored"
    ]
    # Log downloads follow a redirect without leaking the token to it.
    assert ('/blob/logs.zip', None) in github.requests
    assert (f'/repos/{REPO}', f'Bearer {COLLECTOR_TOKEN}') in github.requests
    # Scout's own clone, in its state, is the one pi/claw-rules/repos.md has
    # the model use; the collector keeps working once the model has used it.
    clone = claw.state_dir / 'clones' / REPO
    commands.git(clone, 'fetch', '-q')
    commands.git(clone, 'checkout', '-q', '--detach', 'origin/HEAD')
    assert 'TODO: handle errors' in (clone / 'app.py').read_text()
    assert candidates(scout.collect(claw).text) == found


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
        harness.Reply(
            tool='task_add',
            args={'summary': 'Fix CI', 'description': 'd', 'link': ci_link},
        ),
        harness.Reply(text='added 1'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        github.released.clear()
        trigger = {'type': 'trigger', 'claw': 'scout'}
        triggered = await testing_gateway.ctl(trigger)
        assert isinstance(triggered, dict)
        key = triggered['work_key']
        assert triggered['collecting']
        status = await testing_gateway.ctl({'type': 'status'})
        assert isinstance(status, dict)
        assert f'{key} (collecting)' in render.status(status)
        with pytest.raises(control.ControlError, match='already running'):
            await testing_gateway.ctl(trigger)
        github.released.set()
        first = await fake.wait(key)
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
        upstream = root / 'remote' / f'{REPO}.git'
        commands.git(upstream, 'rm', '-q', 'app.py')
        commit(upstream, 'no more TODOs')
        gateway.trigger('scout', None, None)
        await fake.until(lambda: any('nothing new' in n for n in fake.notices))

    testing_gateway.run_gateway(claws, fake, scenario)
    [added] = json.loads(testing_tasks.task('list', '--json'))
    assert (
        added['summary'],
        added['link'],
        added['namespace'],
        added['tag'],
    ) == ('Fix CI', ci_link, 'bakery', 'triage')
    assert len(fake_llm.requests) == 2
