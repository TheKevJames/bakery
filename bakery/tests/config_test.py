import datetime
import pathlib
import zoneinfo

import pytest

from bakery.gateway import config
from bakery.gateway import scheduler
from testing import gateway as testing_gateway

LISBON = zoneinfo.ZoneInfo('Europe/Lisbon')


def test_repo_defaults_with_claw_overrides(tmp_path: pathlib.Path) -> None:
    claws = testing_gateway.make_claws(
        tmp_path,
        {
            'build': """
                cwd = "~/src"
                [limits]
                cost_usd = 10
                [concurrency]
                max_resumes = 2
                [[job]]
                name = "nightly"
                cron = "0 3 * * *"
                prompt = "go"
                active_hours = "22:00-06:00"
            """
        },
    )

    loaded = config.load(claws)

    assert loaded.gateway.timezone == LISBON
    build = loaded.claw('build')
    assert build.cwd == pathlib.Path.home() / 'src'
    # Overridden keys change; their siblings keep the repo defaults.
    assert (build.limits.cost_usd, build.limits.turns) == (10.0, 50)
    assert (build.concurrency.max_resumes, build.concurrency.max_active) == (
        2,
        1,
    )
    assert build.ask == config.Ask('ask', 4.0, 'park')
    assert build.job('nightly').active_hours == config.ActiveHours(
        datetime.time(22), datetime.time(6)
    )


def test_default_jobs_apply_to_every_claw(tmp_path: pathlib.Path) -> None:
    template = 'name = "{}"\ncron = "0 7 * * *"\nprompt = "p"\n'
    claws = testing_gateway.make_claws(
        tmp_path,
        {
            'plain': '',
            'own': f'[[job]]\n{template.format("daily")}model = "m"\n',
            'replaced': f'[[job]]\n{template.format("dream")}',
        },
    )

    loaded = config.load(claws)

    dream = loaded.claw('plain').job('dream')
    assert (dream.shared, dream.tools, dream.prompt) == (
        True,
        ('memory_edit',),
        '/dream',
    )
    own = loaded.claw('own')
    assert [j.name for j in own.jobs] == ['dream', 'daily']
    assert (own.job('daily').model, own.job('daily').shared) == ('m', False)
    replaced = loaded.claw('replaced').job('dream')
    assert (replaced.shared, replaced.tools) == (False, ())


@pytest.mark.parametrize(
    ('claw_toml', 'error'),
    [
        ('modle = "x"', 'unknown keys modle'),
        ('[limits]\nturns = 0', 'turns: must be >= 1'),
        ('[limits]\ncost_usd = -1', 'cost_usd: must be positive'),
        ('[limits]\nminutes = "30"', 'minutes: wrong type'),
        ('enabled = 1', 'enabled: wrong type'),
        ('[ask]\npolicy = "maybe"', 'policy: must be one of'),
        ('[[job]]\nname = "x"\ncron = "nope"\nprompt = "p"', 'invalid expr'),
        (
            '[[job]]\nname = "x"\ncron = "* * * * *"\nprompt = "p"\n'
            'active_hours = "9-17"',
            'expected HH:MM-HH:MM',
        ),
        ('[[job]]\nname = "x"\ncron = "* * * * *"', 'missing prompt'),
        ('cwd = "${BAKERY_UNSET_VAR}/x"', 'is not set'),
        ('secrets = ["lower"]', 'is not NAME or NAME=ITEM'),
        ('secrets = ["A=b-c"]', 'is not NAME or NAME=ITEM'),
        ('secrets = "X"', 'must be a list of strings'),
        ('[policy]\nconfirm = ["("]', 'bad regex'),
        ('[policy]\nnetwork = ["https://x.com"]', 'is not a domain'),
        ('[policy]\ntool = []', 'unknown keys tool'),
        ('[policy]\nallow_read = ["~/.ssh"]', 'is not under'),
        ('[policy]\ntask_tag = "Bakery/Human"', 'not a lowercase tag path'),
        ('[policy]\ntask_tag = "triage"', 'is not under bakery/'),
        ('[policy]\ntask_tag = "bakery"', 'is not under bakery/'),
        ('[memory]\nmax_chars = 0', 'max_chars: must be >= 1'),
        (
            '[[job]]\nname = "x"\ncron = "* * * * *"\nprompt = "p"\n'
            'tools = "t"',
            'list of strings',
        ),
    ],
)
def test_invalid_config_is_rejected(
    tmp_path: pathlib.Path, claw_toml: str, error: str
) -> None:
    claws = testing_gateway.make_claws(tmp_path, {'a': claw_toml})

    with pytest.raises(config.ConfigError, match=error):
        config.load(claws)


def job(cron: str, hours: str | None = None) -> config.Job:
    active = None
    if hours:
        start, end = hours.split('-')
        active = config.ActiveHours(
            datetime.time.fromisoformat(start),
            datetime.time.fromisoformat(end),
        )
    return config.Job(
        'j',
        cron,
        'p',
        active,
        silent_ok=False,
        model=None,
        thinking=None,
        tools=(),
        collector=None,
        repeat=False,
        watch=None,
        debounce_seconds=120.0,
        shared=False,
    )


def at(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 10, 2, hour, minute, second, tzinfo=LISBON)


@pytest.mark.parametrize(
    ('spec', 'due', 'now', 'runs'),
    [
        (job('0 7 * * *'), at(7), at(7, 0, 1), True),
        (job('0 7 * * *'), at(7), at(7, 0, 59), True),
        # Woke up too late (sleeping Mac, gateway down): skip, no catch-up.
        (job('0 7 * * *'), at(7), at(7, 2), False),
        (job('0 * * * *', '07:00-23:00'), at(6), at(6), False),
        (job('0 * * * *', '07:00-23:00'), at(7), at(7), True),
        (job('0 * * * *', '07:00-23:00'), at(23), at(23), False),
        (job('0 * * * *', '22:00-06:00'), at(23), at(23), True),
        (job('0 * * * *', '22:00-06:00'), at(3), at(3), True),
        (job('0 * * * *', '22:00-06:00'), at(12), at(12), False),
    ],
)
def test_should_run(
    spec: config.Job,
    due: datetime.datetime,
    now: datetime.datetime,
    runs: bool,
) -> None:
    assert scheduler.should_run(spec, due, now) is runs


def test_next_fire_is_local_time_across_dst() -> None:
    # Lisbon leaves summer time on 2026-10-25: 07:00 stays 07:00 local.
    before = datetime.datetime(2026, 10, 24, 8, tzinfo=LISBON)
    fire = scheduler.next_fire(job('0 7 * * *'), before)
    assert fire.isoformat() == '2026-10-25T07:00:00+00:00'
