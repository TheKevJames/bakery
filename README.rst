bakery
======

My agentic harness: the shared `pi`_ configuration I use interactively, plus a
gateway that runs autonomous agents ("claws"). See `docs/DESIGN.md`_.

Layout
------

- ``pi/``: shared extensions, skills, prompts, agents, and rules
- ``interactive/``: the interactive pi profile (``PI_CODING_AGENT_DIR``)
- ``bakery/``: the ``bakery`` CLI (Python, ``uv``)
- ``claws/``: claw profiles and gateway configuration (``defaults.toml``)
- ``bin/vendor``: refresh vendored skills under ``pi/skills/vendor``

Setup
-----

.. code-block:: console

    $ export PI_CODING_AGENT_DIR=~/src/personal/bakery/interactive
    $ bun install --frozen-lockfile --cwd ~/src/personal/bakery/pi
    $ pipx install --force ~/src/personal/bakery/bakery
    $ bakery state init

Gateway
~~~~~~~

The claws' gateway reads its secrets from the macOS Keychain, as generic
passwords with account ``bakery`` and the secret's name as the service. Store
each one with ``security``, which prompts for the value (twice) when ``-w``
comes last, so it never lands in your shell history; ``-U`` replaces an
existing item:

.. code-block:: console

    $ security add-generic-password -U -a bakery -s DISCORD_BOT_TOKEN -w
    $ security add-generic-password -U -a bakery -s ANTHROPIC_API_KEY -w
    $ security add-generic-password -U -a bakery -s JINA_API_KEY -w
    $ security add-generic-password -U -a bakery -s BAKERY_GITHUB_READ -w

- ``DISCORD_BOT_TOKEN``: the ``bakery`` application's bot token (Discord
  developer portal → Bot → Reset Token).
- ``ANTHROPIC_API_KEY``, ``JINA_API_KEY``: the model and web-tool API keys.
- ``BAKERY_GITHUB_READ``: a fine-grained personal access token (GitHub →
  Settings → Developer settings → Fine-grained tokens) for the
  ``TheKevJames`` repositories, with read-only access to metadata, contents,
  issues, pull requests, and actions.

Check which are stored, without printing their values:

.. code-block:: console

    $ for name in DISCORD_BOT_TOKEN ANTHROPIC_API_KEY JINA_API_KEY BAKERY_GITHUB_READ; do
    >     security find-generic-password -a bakery -s "$name" >/dev/null 2>&1 \
    >         && echo "$name: ok" || echo "$name: missing"
    > done

Then check the Discord connection and start the gateway as a launchd agent
(``bakery service restart`` after changing secrets):

.. code-block:: console

    $ bakery gateway check
    $ bakery service install

.. _docs/DESIGN.md: docs/DESIGN.md
.. _pi: https://github.com/earendil-works/pi
