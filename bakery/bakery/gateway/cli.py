"""`bakery` subcommands that control the running gateway."""

import argparse
import asyncio
import pathlib
import sys
from collections.abc import Callable
from collections.abc import Coroutine
from typing import Any

from .. import paths
from .. import secrets
from .. import service
from ..chat import render
from . import config
from . import control
from . import main


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


def do_status(args: argparse.Namespace) -> int:
    _ = args
    print(render.status(_call({'type': 'status'})))
    return 0


def do_budget(args: argparse.Namespace) -> int:
    _ = args
    print(render.budget(_call({'type': 'budget'})))
    return 0


def _claws_dir(args: argparse.Namespace) -> pathlib.Path:
    if args.claws_dir:
        return pathlib.Path(args.claws_dir).expanduser().resolve()
    try:
        return paths.repo() / 'claws'
    except paths.PathError as e:
        sys.exit(str(e))


def _run_async(coroutine: Coroutine[None, None, None]) -> int:
    try:
        asyncio.run(coroutine)
    except (config.ConfigError, secrets.SecretError) as e:
        sys.exit(str(e))
    return 0


def do_run(args: argparse.Namespace) -> int:
    main.configure_logging()
    return _run_async(main.run(_claws_dir(args)))


def do_check(args: argparse.Namespace) -> int:
    code = _run_async(main.check(_claws_dir(args)))
    print('connected; see #bakery')
    return code


def do_service(args: argparse.Namespace) -> int:
    try:
        if args.service_command == 'install':
            print(f'installed {service.install()}')
        elif args.service_command == 'uninstall':
            service.uninstall()
            print('uninstalled')
        else:
            service.restart()
            print('restarted')
    except (service.ServiceError, paths.PathError) as e:
        sys.exit(str(e))
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
    for name, func, help_ in (
        ('run', do_run, 'run the gateway in the foreground'),
        ('check', do_check, 'test the Discord connection and setup'),
    ):
        parser = gateway_sub.add_parser(name, help=help_)
        parser.add_argument(
            '--claws-dir', help='default: claws/ in the bakery checkout'
        )
        parser.set_defaults(func=func)

    service_parser = add_parser(
        'service', help='manage the launchd agent running the gateway'
    )
    service_parser.add_argument(
        'service_command', choices=('install', 'uninstall', 'restart')
    )
    service_parser.set_defaults(func=do_service)
