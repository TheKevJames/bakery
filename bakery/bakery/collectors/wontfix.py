"""
Rejections: lineages scout and baker never file again.

Kevin records one from a task with `bakery wontfix add`, then closes the task
as usual. Done tasks are deleted, so this store is all that remains of them.
It lives in the claw state repo, which commits every change.
"""

import argparse
import dataclasses
import datetime
import json
import pathlib
import sys
from collections.abc import Callable

from .. import state
from . import tasks


class WontfixError(ValueError):
    pass


@dataclasses.dataclass(frozen=True)
class Rejection:
    summary: str
    reason: str
    date: str


def path() -> pathlib.Path:
    return state.root() / state.SHARED / 'wontfix.json'


def load() -> dict[str, Rejection]:
    """Rejections by lineage (see `tasks.lineage`)."""
    if not path().exists():
        return {}
    data: dict[str, dict[str, str]] = json.loads(path().read_text())
    return {link: Rejection(**fields) for link, fields in data.items()}


def _save(rejections: dict[str, Rejection], message: str) -> None:
    data = {link: dataclasses.asdict(r) for link, r in rejections.items()}
    state.init()
    path().write_text(json.dumps(data, indent=2, sort_keys=True) + '\n')
    state.commit(message)


def add(ident: int, reason: str) -> str:
    """Reject task `ident`'s lineage; returns the lineage."""
    if not reason.strip():
        raise WontfixError('the reason is empty')
    try:
        task = tasks.shown(ident)
    except tasks.TaskError as e:
        raise WontfixError(str(e)) from e
    if not task['link']:
        raise WontfixError(f'task {ident} has no link')
    link = tasks.lineage(str(task['link']))
    rejections = load()
    if link in rejections:
        raise WontfixError(f'{link} is already rejected')
    today = datetime.datetime.now(datetime.UTC).date().isoformat()
    rejections[link] = Rejection(str(task['summary']), reason.strip(), today)
    _save(rejections, f'wontfix: add {link}')
    return link


def remove(link: str) -> None:
    rejections = load()
    if rejections.pop(link, None) is None:
        raise WontfixError(f'{link} is not rejected')
    _save(rejections, f'wontfix: rm {link}')


def do_add(args: argparse.Namespace) -> int:
    try:
        print(f'rejected {add(args.id, args.reason)}')
    except WontfixError as e:
        sys.exit(f'cannot reject #{args.id}: {e}')
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
    for link, r in sorted(load().items()):
        print(f'{r.date} {link}\n    {r.summary}\n    {r.reason}')
    return 0


def add_parsers(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    sub = add_parser(
        'wontfix', help='links scout and baker never file again'
    ).add_subparsers(dest='wontfix_command')
    add_ = sub.add_parser('add', help="reject a task's link; keeps the task")
    add_.add_argument('id', type=int, help='task id')
    add_.add_argument('--reason', required=True, help='why, in one line')
    add_.set_defaults(func=do_add)
    rm = sub.add_parser('rm', help='file a rejected link again')
    rm.add_argument('link', help='as `bakery wontfix list` shows it')
    rm.set_defaults(func=do_rm)
    sub.add_parser('list', help='every rejection').set_defaults(func=do_list)
