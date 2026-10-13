import pathlib

from bakery.gateway import core
from testing import gateway as testing_gateway
from testing import harness
from testing import tasks as testing_tasks


def test_task_claims_and_adds(
    root: pathlib.Path, fake_llm: harness.FakeLLM
) -> None:
    testing_tasks.create(root / 'tasks')
    testing_tasks.task('add', 'mine', '-n', 'bakery', '--tag', 'triage')
    testing_tasks.task(
        'add', 'theirs', '-n', 'bakery', '--tag', 'triage',
        '--owner', 'operator',
    )  # fmt: skip
    testing_tasks.task('add', "the operator's")
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
    assert "1: the operator's" in results[5]
    assert 'attempt to write a readonly database' in results[6]
    assert testing_tasks.show('1')['summary'] == "the operator's"
    final = testing_tasks.show('bakery:1')
    assert (final['owner'], final['tag'], final['description']) == (
        None,
        'build',
        'n',
    )
    added = testing_tasks.show('bakery:3')
    assert (added['tag'], added['priority'], added['size']) == (
        'human',
        'high',
        'small',
    )
