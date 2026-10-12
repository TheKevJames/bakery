"""
Secrets for the gateway and its claws.

Each secret's value comes only from the macOS Keychain: a generic password
with account `bakery` and the secret's name as its service. Never from the
gateway's environment, which holds all of my shell's secrets when the gateway
is started from a shell.
"""

import shutil
import subprocess

KEYCHAIN_ACCOUNT = 'bakery'


class SecretError(Exception):
    pass


def keychain(name: str) -> str | None:
    if shutil.which('security') is None:
        return None
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
    value = keychain(name)
    if not value:
        raise SecretError(
            f'secret {name} is not set; store it with: security'
            f' add-generic-password -U -a {KEYCHAIN_ACCOUNT} -s {name} -w'
        )
    return value
