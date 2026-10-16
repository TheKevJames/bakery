"""
Rejections: lineages the claws never file again (see `filing`).

The operator records one from a task with `bakery wontfix add`, then closes
the task as usual. Done tasks are deleted, so this store is all that remains
of them. It lives in the claw state repo, which commits every change.
"""

import argparse
import dataclasses
import datetime
import json
import pathlib
import sys
from collections.abc import Callable

from .. import state
from . import lineage
from . import store


class WontfixError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Rejection:
    # The rejected task's link, without its line anchor.
    link: str
    summary: str
    reason: str
    date: str


def path() -> pathlib.Path:
    return state.root() / state.SHARED / 'wontfix.json'


def load() -> dict[str, Rejection]:
    """Rejections by lineage."""
    if not path().exists():
        return {}
    data: dict[str, dict[str, str]] = json.loads(path().read_text())
    return {
        lineage.of(link): Rejection(link, **fields)
        for link, fields in data.items()
    }


def _save(rejections: dict[str, Rejection], message: str) -> None:
    data = {
        r.link: {k: v for k, v in dataclasses.asdict(r).items() if k != 'link'}
        for r in rejections.values()
    }
    state.init()
    path().write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    state.commit(message, str(path().relative_to(state.root())))


def add(address: str, reason: str) -> str:
    """Reject the lineage of task `[namespace:]id`; returns its link."""
    if not reason.strip():
        raise WontfixError('the reason is empty')
    try:
        task = store.shown(address)
    except store.TaskError as e:
        raise WontfixError(str(e)) from e
    if not task['link']:
        raise WontfixError(f'task {address} has no link')
    link = lineage.source(str(task['link']))
    rejections = load()
    rejected = rejections.get(lineage.of(link))
    if rejected is not None:
        raise WontfixError(f'{rejected.link} is already rejected')
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    rejections[lineage.of(link)] = Rejection(
        link, str(task['summary']), reason.strip(), today
    )
    _save(rejections, f'wontfix: add {link}')
    return link


def remove(link: str) -> None:
    """Unreject `link`'s lineage, however `link` is anchored or cased."""
    rejections = load()
    rejected = rejections.pop(lineage.of(link), None)
    if rejected is None:
        raise WontfixError(f'{link} is not rejected')
    _save(rejections, f'wontfix: rm {rejected.link}')


def do_add(args: argparse.Namespace) -> int:
    try:
        print(f'rejected {add(args.id, args.reason)}')
    except WontfixError as e:
        sys.exit(f'cannot reject {args.id}: {e}')
    return 0


def do_rm(args: argparse.Namespace) -> int:
    try:
        remove(args.link)
    except WontfixError as e:
        sys.exit(f'cannot unreject: {e}')
    print(f'unrejected {args.link}')
    return 0


def do_list(args: argparse.Namespace) -> int:
    _ = args
    for _, r in sorted(load().items()):
        print(f'{r.date} {r.link}\n    {r.summary}\n    {r.reason}')
    return 0


def add_parsers(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    sub = add_parser(
        'wontfix', help='links the claws never file again'
    ).add_subparsers(dest='wontfix_command')
    add_ = sub.add_parser('add', help="reject a task's link; keeps the task")
    add_.add_argument(
        'id', help='task id, as `task` takes it: `[namespace:]id`'
    )
    add_.add_argument('--reason', required=True, help='why, in one line')
    add_.set_defaults(func=do_add)
    rm = sub.add_parser('rm', help='file a rejected link again')
    rm.add_argument(
        'link', help='any link with its lineage, eg. as `list` shows it'
    )
    rm.set_defaults(func=do_rm)
    sub.add_parser('list', help='every rejection').set_defaults(func=do_list)
