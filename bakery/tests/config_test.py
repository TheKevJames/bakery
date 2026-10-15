import datetime
import json
import pathlib
import tomllib
import zoneinfo

import pytest

from bakery import state
from bakery.collectors import scout_repos
from bakery.gateway import config
from bakery.gateway import scheduler
from testing import gateway as testing_gateway
from testing import harness

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

    defaults = tomllib.loads((claws / 'defaults.toml').read_text())
    claw = defaults['claw']
    assert loaded.gateway.timezone == zoneinfo.ZoneInfo(
        defaults['gateway']['timezone']
    )
    build = loaded.claw('build')
    assert build.cwd == pathlib.Path.home() / 'src'
    # Overridden keys change; their siblings keep the repo defaults.
    assert (build.limits.cost_usd, build.limits.turns) == (
        10.0,
        claw['limits']['turns'],
    )
    assert (build.concurrency.max_resumes, build.concurrency.max_active) == (
        2,
        claw['concurrency']['max_active'],
    )
    assert build.ask == config.Ask(**claw['ask'])
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
    assert (dream.shared, dream.prompt) == (True, '/dream')
    assert (dream.collector, dream.skip_empty) == ('changes', False)
    own = loaded.claw('own')
    assert [j.name for j in own.jobs] == ['dream', 'daily']
    assert (own.job('daily').model, own.job('daily').shared) == ('m', False)
    replaced = loaded.claw('replaced').job('dream')
    assert not replaced.shared


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
        ('bash_secrets = ["GH_TOKEN"]', 'GH_TOKEN is not in its secrets'),
        ('[policy]\nnetwork = []', 'unknown keys network'),
        ('[policy]\ntools = []', 'unknown keys tools'),
        ('[policy]\nallow_read = ["~/.ssh"]', 'unknown keys allow_read'),
        ('[tasks]\ntag = "Human"', 'not a lowercase tag'),
        ('[tasks]\ntag = "bakery/human"', 'not a lowercase tag'),
        ('[memory]\nmax_chars = 0', 'max_chars: must be >= 1'),
        (
            '[[job]]\nname = "x"\ncron = "* * * * *"\nprompt = "p"\n'
            'tools = []',
            'unknown keys tools',
        ),
        (
            '[[job]]\nname = "x"\ncron = "* * * * *"\nprompt = "p"\n'
            'thinking = "extreme"',
            'thinking: must be one of',
        ),
        (
            '[[job]]\nname = "x"\ncron = "* * * * *"\nprompt = "p"\n'
            'skip_empty = false',
            'skip_empty: needs a collector',
        ),
    ],
)
def test_invalid_config_is_rejected(
    tmp_path: pathlib.Path, claw_toml: str, error: str
) -> None:
    claws = testing_gateway.make_claws(tmp_path, {'a': claw_toml})

    with pytest.raises(config.ConfigError, match=error):
        config.load(claws)


def test_job_queue_options(tmp_path: pathlib.Path) -> None:
    claws = testing_gateway.make_claws(
        tmp_path,
        {
            'a': """
                [[job]]
                name = "q"
                cron = "0 * * * *"
                prompt = "p"
                collector = "triage"
                repeat = true
                watch = "~/tasks"
            """
        },
    )

    job = config.load(claws).claw('a').job('q')

    assert (job.repeat, job.watch, job.debounce_seconds, job.skip_empty) == (
        True,
        pathlib.Path.home() / 'tasks',
        config.DEFAULT_DEBOUNCE_SECONDS,
        True,
    )


def test_repo_claws_load(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv('TASK_FOLDER', str(tmp_path))  # triage watches it
    monkeypatch.setenv('XDG_STATE_HOME', str(tmp_path))  # baker reads it
    loaded = config.load(harness.REPO / 'claws')
    claw = loaded.claw('scout')
    settings = scout_repos.load_settings(claw.profile)
    assert claw.job('daily').collector == 'scout'
    assert 'TheKevJames/bakery' in settings.excludes
    for profile in sorted((harness.REPO / 'claws').glob('*/')):
        settings = json.loads((profile / 'settings.json').read_text())
        for path in settings['extensions'] + settings['prompts']:
            if path.startswith(('-builtin:', '+builtin:')):
                continue  # pi's built-in extensions, by name
            assert (profile / path).exists(), path
    queue = loaded.claw('triage').job('queue')
    assert (queue.collector, queue.repeat) == ('triage', True)
    baker = loaded.claw('baker')
    assert baker.job('review').collector == 'baker'
    assert baker.tasks.tag == 'human'
    assert state.root() in baker.policy.read_paths


def load_job(
    tmp_path: pathlib.Path, cron: str, hours: str | None
) -> config.Job:
    """Job `j`, as loaded from a claw.toml."""
    active = f'active_hours = "{hours}"\n' if hours else ''
    claws = testing_gateway.make_claws(
        tmp_path,
        {'a': f'[[job]]\nname = "j"\ncron = "{cron}"\nprompt = "p"\n{active}'},
    )
    return config.load(claws).claw('a').job('j')


def at(hour: int, minute: int = 0, second: int = 0) -> datetime.datetime:
    return datetime.datetime(2026, 10, 2, hour, minute, second, tzinfo=LISBON)


@pytest.mark.parametrize(
    ('cron', 'hours', 'due', 'now', 'runs'),
    [
        ('0 7 * * *', None, at(7), at(7, 0, 1), True),
        ('0 7 * * *', None, at(7), at(7, 0, 59), True),
        # Woke up too late (sleeping Mac, gateway down): skip, no catch-up.
        ('0 7 * * *', None, at(7), at(7, 2), False),
        ('0 * * * *', '07:00-23:00', at(6), at(6), False),
        ('0 * * * *', '07:00-23:00', at(7), at(7), True),
        ('0 * * * *', '07:00-23:00', at(23), at(23), False),
        ('0 * * * *', '22:00-06:00', at(23), at(23), True),
        ('0 * * * *', '22:00-06:00', at(3), at(3), True),
        ('0 * * * *', '22:00-06:00', at(12), at(12), False),
    ],
)
def test_should_run(
    tmp_path: pathlib.Path,
    cron: str,
    hours: str | None,
    due: datetime.datetime,
    now: datetime.datetime,
    runs: bool,
) -> None:
    spec = load_job(tmp_path, cron, hours)
    assert scheduler.should_run(spec, due, now) is runs


def test_next_fire_is_local_time_across_dst(tmp_path: pathlib.Path) -> None:
    # Lisbon leaves summer time on 2026-10-25: 07:00 stays 07:00 local.
    before = datetime.datetime(2026, 10, 24, 8, tzinfo=LISBON)
    fire = scheduler.next_fire(load_job(tmp_path, '0 7 * * *', None), before)
    assert fire.isoformat() == '2026-10-25T07:00:00+00:00'
