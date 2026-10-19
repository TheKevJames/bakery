"""
Claims the claws leave behind.

A claw's claim never outlives its runs: once a claw has no run in flight, the
gateway releases any task it still owns, whether its run failed, was
interrupted, or forgot to release it. Otherwise the task would wait forever,
as queues only hand over unowned tasks.
"""

import subprocess

from . import store


def release(claw: str) -> tuple[list[str], list[str]]:
    """
    Release every task in the claws' namespace that `claw` owns. Returns the
    addresses released, and problems.
    """
    try:
        owned = store.listed('-f', f'namespace={store.NAMESPACE},owner={claw}')
    except subprocess.CalledProcessError as e:
        return [], [f'cannot list tasks: {e.stderr.strip()}']
    released: list[str] = []
    errors: list[str] = []
    for task in owned:
        address = store.ref(task)
        result = subprocess.run(
            ('task', 'unset', address, 'owner', '--json'),
            capture_output=True,
            text=True,
            check=False,
        )
        if result.returncode:
            errors.append(f'{address}: {result.stderr.strip()}')
        else:
            released.append(address)
    return released, errors
