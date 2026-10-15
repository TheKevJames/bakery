import re

import hypothesis
import pytest
from hypothesis import strategies as st

from bakery.gateway import prompts
from testing import harness

# What pi substitutes into a template from its shell-parsed arguments, which
# the gateway never passes.
PI_ARGUMENTS_RE = re.compile(r'\$(\d|@|ARGUMENTS|\{)')


@pytest.fixture(name='repo', scope='function', autouse=True)
def fixture_repo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('BAKERY_REPO', str(harness.REPO))


def test_a_template_becomes_its_body() -> None:
    body = prompts.expand('/dream')

    assert body.startswith('It is time to consolidate your memory')
    assert '---' not in body
    assert body == body.strip()


@hypothesis.settings(
    suppress_health_check=[hypothesis.HealthCheck.function_scoped_fixture]
)
@hypothesis.given(text=st.text(min_size=1).filter(lambda t: t[0].strip()))
def test_text_after_a_template_arrives_verbatim(text: str) -> None:
    body = prompts.expand('/dream')

    for separator in (' ', '\n\n', '\t \n'):
        assert prompts.expand(f'/dream{separator}{text}') == (
            f'{body}\n\n{text}'
        )


@pytest.mark.parametrize(
    'text',
    [
        'Triage this task.',
        'see /dream',
        '/compact',
        '/compact now',
        '/../claw-rules/memory',
        '/dream.md',
        '',
    ],
)
def test_anything_else_is_left_to_pi(text: str) -> None:
    assert prompts.expand(text) == text


def test_claw_templates_take_no_pi_arguments() -> None:
    templates = sorted(prompts.directory().glob('*.md'))

    assert templates
    for template in templates:
        assert not PI_ARGUMENTS_RE.search(template.read_text()), template
