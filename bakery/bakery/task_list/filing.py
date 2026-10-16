"""
Filing: adding a task unless an open task or a rejection has its lineage.

`bakery task add` is `task add` with that check; the claws' tools file only
through it. Collectors, which weigh many candidates at once, use `tracked`.
"""

import argparse
import dataclasses
import json
import subprocess
import sys
from collections.abc import Callable

from . import lineage
from . import store
from . import wontfix

Task = dict[str, object]


def tracked() -> set[str]:
    """Lineages not to file again: open tasks' and rejections'."""
    return {
        lineage.of(str(task['link']))
        for task in store.listed()
        if task.get('link')
    } | set(wontfix.load())


def holder(link: str) -> Task | wontfix.Rejection | None:
    """
    What has `link`'s lineage: an open task (in any namespace), else a
    rejection; None if `link` is new.
    """
    key = lineage.of(link)
    # `link~` is a substring match, so it also finds eg. `…/issues/12` for
    # `…/issues/1`.
    for task in store.listed('-f', f'link~{lineage.source(link)}'):
        if lineage.of(str(task['link'])) == key:
            return task
    return wontfix.load().get(key)


def _report(held: Task | wontfix.Rejection, as_json: bool) -> None:
    if isinstance(held, wontfix.Rejection):
        outcome = {'rejected': dataclasses.asdict(held)}
        line = f'rejected on {held.date} ({held.link}): {held.reason}'
    else:
        outcome = {'open': held}
        line = f'open as {store.ref(held)}: {held["summary"]}'
    print(json.dumps(outcome, indent=2) if as_json else line)


def do_add(args: argparse.Namespace) -> int:
    # Ours go first: `task_args` may end with `-- <summary>`.
    ours = ['--json'] if args.json else []
    if args.link is not None:
        # `task` splits filters on commas, so its lineage could not be found.
        if ',' in args.link:
            sys.exit(f'cannot file {args.link}: a link cannot hold a comma')
        held = holder(args.link)
        if held is not None:
            _report(held, args.json)
            return 0
        ours += ['--link', args.link]
    result = subprocess.run(
        ('task', 'add', *ours, *args.task_args),
        capture_output=True,
        text=True,
        check=False,
    )
    sys.stderr.write(result.stderr)
    if result.returncode:
        return result.returncode
    if args.json:
        print(json.dumps({'filed': json.loads(result.stdout)}, indent=2))
    else:
        sys.stdout.write(result.stdout)
    return 0


def add_parsers(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    add = (
        add_parser('task', help='the shared task list')
        .add_subparsers(dest='task_command')
        .add_parser(
            'add',
            help='`task add`, unless an open task or a rejection has the'
            " link's lineage",
            description='Any other argument is passed to `task add`. With'
            ' --json, prints {"filed": task}, {"open": task}, or'
            ' {"rejected": rejection}.',
        )
    )
    add.add_argument('--link')
    add.add_argument('--json', action='store_true')
    # Filled with the arguments `bakery` does not know (see cli.main).
    add.set_defaults(func=do_add, task_args=[])
