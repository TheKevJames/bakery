import json
import pathlib

from bakery.gateway import core
from bakery.task_list import wontfix
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks


def test_task_claims_and_adds(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    testing_tasks.task('add', 'mine', '-n', 'bakery', '--tag', 'triage')
    testing_tasks.task(
        'add', 'theirs', '-n', 'bakery', '--tag', 'triage',
        '--owner', 'operator',
    )  # fmt: skip
    testing_tasks.task('add', "the operator's")
    testing_tasks.task('add', '--link', 'https://x/gone', 'rejected')
    wontfix.add('2', 'not ours')
    testing_tasks.task('2', 'done')
    claws = testing_gateway.make_claws(
        root,
        {
            'triage': """
                [tasks]
                tag = "human"
            """
        },
        extensions={'triage': ['pi/claw-extensions/task.ts']},
    )
    fake_llm.queue(
        harness.call('task_set', id=1, claim=True, tag='build'),
        harness.call('task_set', id=2, claim=True),
        harness.call('task_set', id=2, release=True),
        harness.call('task_set', id=1, release=True, description_append='n'),
        harness.call(
            'task_add',
            summary='new',
            description='d',
            link='baker:plumbing/x',
            priority='high',
            size='small',
        ),
        harness.call(
            'task_add',
            summary='again',
            description='d',
            link='BAKER:plumbing/x',
        ),
        harness.call(
            'task_add', summary='no', description='d', link='https://x/gone#L1'
        ),
        # The sandbox lets claws read every namespace, but write only theirs.
        harness.call('bash', command='task list -f namespace=default'),
        harness.call('bash', command='task 1 set --summary mine'),
        harness.Reply(text='done'),
    )
    fake = testing_gateway.FakeChannel()

    async def scenario(gateway: core.Gateway) -> None:
        done = await fake.wait(gateway.trigger('triage', None, 'go'))
        assert done.status == 'settled', done.reason

    testing_gateway.run_gateway(claws, fake, scenario)
    results = fake_llm.tool_results()
    assert '"owner": "triage"' in results[0]
    assert 'owned by operator' in results[1]
    assert 'not claimed by triage' in results[2]
    assert '"owner": null' in results[3]
    assert '"id":3,' in results[4]
    assert 'open task #bakery:3 already has' in results[5]
    assert 'the operator rejected' in results[6] and 'not ours' in results[6]
    assert "1: the operator's" in results[7]
    assert 'attempt to write a readonly database' in results[8]
    assert testing_tasks.show('1')['summary'] == "the operator's"
    final = testing_tasks.show('bakery:1')
    assert (final['owner'], final['tag'], final['description']) == (
        None,
        'build',
        'n',
    )
    claws_tasks = testing_tasks.task(
        'list', '--json', '-f', 'namespace=bakery'
    )
    assert len(json.loads(claws_tasks)) == 3
    added = testing_tasks.show('bakery:3')
    assert (added['tag'], added['priority'], added['size']) == (
        'human',
        'high',
        'small',
    )
