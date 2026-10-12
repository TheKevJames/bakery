"""
A reminder before the claws' GitHub token expires, so the claws don't
silently lose GitHub. GitHub reports a classic token's expiry with every API
response; the scout collector, which calls the API daily, passes it on.
"""

import datetime
import pathlib

from .. import state

REMIND_DAYS = 30


def _marker() -> pathlib.Path:
    """The expiry already reminded about: once per token."""
    return state.root() / '_gateway' / 'github-token-expiry'


def reminder(
    expires: datetime.datetime | None, now: datetime.datetime | None = None
) -> list[str]:
    """A problem to tell me about, the first time expiry is near."""
    if expires is None:
        return []
    now = now or datetime.datetime.now(datetime.UTC)
    if expires - now > datetime.timedelta(days=REMIND_DAYS):
        return []
    marker = _marker()
    stamp = expires.isoformat()
    if marker.exists() and marker.read_text().strip() == stamp:
        return []
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(f'{stamp}\n')
    return [
        f"the claws' GitHub token (BAKERY_GITHUB) expires on"
        f' {expires:%Y-%m-%d}; create a new one and store it (see README.rst)'
    ]
