"""
Secrets for the gateway and its claws.

Each secret is an environment-variable name. Its value comes from the
gateway's own environment if set (eg. when run from a shell), else from the
macOS Keychain: a generic password with account `bakery` and the name as its
service. Under launchd the environment is minimal, so the Keychain is the
normal source.
"""

import os
import subprocess

KEYCHAIN_ACCOUNT = 'bakery'


class SecretError(Exception):
    pass


def keychain(name: str) -> str | None:
    result = subprocess.run(
        (
            'security',
            'find-generic-password',
            '-a',
            KEYCHAIN_ACCOUNT,
            '-s',
            name,
            '-w',
        ),
        capture_output=True,
        text=True,
        check=False,
    )
    value = result.stdout.strip()
    return value if result.returncode == 0 and value else None


def get(name: str) -> str:
    value = os.environ.get(name) or keychain(name)
    if not value:
        raise SecretError(
            f'secret {name} is not set; store it with: security'
            f' add-generic-password -U -a {KEYCHAIN_ACCOUNT} -s {name} -w'
        )
    return value
