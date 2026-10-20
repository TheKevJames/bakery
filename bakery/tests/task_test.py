import json
import pathlib
import shlex

from bakery.gateway import core
from bakery.task_list import wontfix
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks

ADD = ('task add -n bakery --tag triage --description d --json',)
QUESTIONS = (
    'Gap.\n\n## Questions (2026-01-01)\n\n1. Accept this proposal as is?\n'
    '   **Context:** it helps\n   **Default:** accept'
)


def bash(command: str) -> harness.Reply:
    return harness.call('bash', command=command)


def test_claws_write_the_task_list_only_through_their_rules(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    testing_tasks.task('add', 'mine', '-n', 'bakery', '--tag', 'triage')
    testing_tasks.task('add', 'theirs', '-n', 'bakery', '--tag', 'triage')
    testing_tasks.task('bakery:2', 'claim')
    testing_tasks.task('add', "the operator's")
    testing_tasks.task('add', '--link', 'https://x/gone', 'rejected')
    wontfix.add('2', 'not ours')
    testing_tasks.task('2', 'done')
    claws = testing_gateway.make_claws(
        root,
        {'triage': '', 'scout': ''},
        rules={'triage': 'triage', 'scout': 'scout'},
    )
    fake_llm.queue(
        bash('task bakery:1 set --tag build --json'),
        bash('task bakery:2 set --tag build'),
        bash('task bakery:1 set --tag human'),
        bash('task bakery:1 set --description-append n --json'),
        bash(f'{ADD[0]} new'),
        # Whatever enters human, created or moved, asks the operator.
        bash('task add -n bakery --tag human --description d --json unasked'),
        bash(
            shlex.join(
                (
                    'task',
                    'add',
                    '-n',
                    'bakery',
                    '--tag',
                    'human',
                    '--json',
                    '--description',
                    QUESTIONS,
                    'asked',
                )
            )
        ),  # fmt: skip
        bash('task list -f namespace=default'),
        bash('task 1 set --summary mine'),
        # Around the server, the sandbox leaves the task list read-only.
        bash('TASK_SERVER= task bakery:1 set --summary mine --force'),
        harness.Reply(text='done'),
        # Scout files only new links, into triage.
        bash(f'{ADD[0]} --link baker:plumbing/x new'),
        bash(f'{ADD[0]} --link BAKER:plumbing/x again'),
        bash(f'{ADD[0]} --link https://x/gone#L1 no'),
        bash(f'{ADD[0]} --tag build --link l built'),
        # Scout completes only what it added this run.
        bash(f'{ADD[0]} --link baker:plumbing/y mistaken'),
        bash('task bakery:6 done --json'),
        bash('task bakery:1 done --json'),
        # Triage sets priority; every new task has a description.
        bash(f'{ADD[0]} --link baker:plumbing/z --priority high prioritized'),
        bash('task add -n bakery --tag triage --link baker:plumbing/w bare'),
        harness.Reply(text='done'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        for claw in ('triage', 'scout'):
            done = await fake.wait(gateway.trigger(claw, None, 'go'))
            assert done.status == 'settled', done.reason

    testing_gateway.run_gateway(claws, fake, scenario)
    # Each run sees only its own tool results: triage's 10, then scout's.
    results = fake_llm.tool_results(10) + fake_llm.tool_results()
    print('\n---\n'.join(results))
    assert '"claim": "triage/' in results[0]
    assert 'claimed by operator' in results[1]
    assert 'enters human only with a new `## Questions' in results[2]
    assert '"description": "n"' in results[3]
    assert 'you may not set link' not in results[4]
    assert '"id": 3,' in results[4] and '"claim": "triage/' in results[4]
    assert 'enters human only with a new `## Questions' in results[5]
    assert '"id": 4,' in results[6] and '"tag": "human"' in results[6]
    assert "1: the operator's" in results[7]
    assert 'you may not claim tasks in default > triage' in results[8]
    assert 'readonly database' in results[9]
    assert '"id": 5,' in results[10] and '"claim": "scout/' in results[10]
    assert 'open task bakery:5 already has' in results[11]
    assert 'the operator rejected' in results[12] and 'not ours' in results[12]
    assert 'you may not create tasks in bakery > build' in results[13]
    assert '"id": 6,' in results[14]
    assert results[15].strip() == 'null'
    assert 'you may not claim tasks in bakery > build' in results[16]
    assert 'you may not set priority' in results[17]
    assert 'a new task needs its description' in results[18]
    assert testing_tasks.show('1')['summary'] == "the operator's"
    # The run's claims ended with it; the operator's did not.
    final = testing_tasks.show('bakery:1')
    assert (final['claim'], final['tag'], final['description']) == (
        None,
        'build',
        'n',
    )
    assert testing_tasks.show('bakery:2')['claim'] == 'operator'
    claws_tasks = testing_tasks.task(
        'list', '--json', '-f', 'namespace=bakery'
    )
    assert len(json.loads(claws_tasks)) == 5
    added = testing_tasks.show('bakery:5')
    assert added['claim'] is None
