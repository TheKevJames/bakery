"""`bakery` subcommands that control the running gateway."""

import argparse
import sys
import time
from collections.abc import Callable
from typing import Any

from . import control


def _call(record: dict[str, object]) -> dict[str, Any]:
    try:
        data = control.send(record)
    except control.ControlError as e:
        sys.exit(str(e))
    return data if isinstance(data, dict) else {}


def do_trigger(args: argparse.Namespace) -> int:
    data = _call(
        {
            'type': 'trigger',
            'claw': args.claw,
            'job': args.job,
            'prompt': args.prompt,
        }
    )
    print(f'queued {data["work_key"]}')
    return 0


def do_pause(args: argparse.Namespace) -> int:
    _call({'type': 'pause', 'target': args.target, 'abort': args.abort})
    print(f'paused {args.target}')
    return 0


def do_resume(args: argparse.Namespace) -> int:
    _call({'type': 'resume', 'target': args.target})
    print(f'resumed {args.target}')
    return 0


def do_reload(args: argparse.Namespace) -> int:
    _ = args
    _call({'type': 'reload'})
    print('reloaded')
    return 0


def _run_line(run: dict[str, Any]) -> str:
    elapsed = int(time.time() - run['started_at'])
    tool = f' [{run["current_tool"]}]' if run['current_tool'] else ''
    return (
        f'    {run["work_key"]}{tool}  {run["turns"]} turns'
        f'  ${run["cost_usd"]:.2f}  {elapsed // 60}m{elapsed % 60:02d}s'
    )


def do_status(args: argparse.Namespace) -> int:
    _ = args
    data = _call({'type': 'status'})
    print(f'processes: {data["processes"]}')
    for name, claw in data['claws'].items():
        state = 'disabled' if not claw['enabled'] else 'enabled'
        state = 'paused' if claw['paused'] else state
        print(f'{name}: {state}')
        for run in claw['active']:
            print(_run_line(run))
        for key in claw['queued']:
            print(f'    {key} (queued)')
        for job, due in sorted(claw['next'].items()):
            print(f'    next {job}: {due}')
    return 0


def do_budget(args: argparse.Namespace) -> int:
    _ = args
    data = _call({'type': 'budget'})
    rows = [('all', data['global'])] + sorted(data['claws'].items())
    width = max(len(name) for name, _ in rows)
    for name, row in rows:
        print(
            f'{name:<{width}}  ${row["spent_usd"]:.2f}'
            f' / ${row["limit_usd"]:.2f} today'
        )
    return 0


def add_parsers(add_parser: Callable[..., argparse.ArgumentParser]) -> None:
    trigger = add_parser('trigger', help='run a claw job now')
    trigger.add_argument('claw')
    trigger.add_argument('job', nargs='?', help='default: the only job')
    trigger.add_argument('--prompt', help='custom prompt instead of the job')
    trigger.set_defaults(func=do_trigger)

    pause = add_parser('pause', help='stop starting runs for a claw')
    pause.add_argument('target', help='claw name or "all"')
    pause.add_argument(
        '--abort', action='store_true', help='also park in-flight runs'
    )
    pause.set_defaults(func=do_pause)

    resume = add_parser(
        'resume', help='unpause a claw, or re-run a parked unit of work'
    )
    resume.add_argument('target', help='claw name, "all", or a work key')
    resume.set_defaults(func=do_resume)

    add_parser('status', help='show gateway status').set_defaults(
        func=do_status
    )
    add_parser('budget', help="show today's spend").set_defaults(
        func=do_budget
    )

    gateway_sub = add_parser(
        'gateway', help='manage the gateway'
    ).add_subparsers(dest='gateway_command')
    gateway_sub.add_parser(
        'reload', help='reload claw configuration'
    ).set_defaults(func=do_reload)
