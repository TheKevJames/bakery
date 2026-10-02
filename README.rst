bakery
======

My agentic harness: the shared `pi`_ configuration I use interactively, plus a
gateway that runs autonomous agents ("claws"). See `docs/DESIGN.md`_.

Layout
------

- ``pi/``: shared extensions, skills, prompts, and agents
- ``interactive/``: the interactive pi profile (``PI_CODING_AGENT_DIR``)
- ``bakery/``: the ``bakery`` CLI (Python, ``uv``)
- ``bin/vendor``: refresh vendored skills under ``pi/skills/vendor``

Setup
-----

.. code-block:: console

    $ export PI_CODING_AGENT_DIR=~/src/personal/bakery/interactive
    $ pipx install --force ~/src/personal/bakery/bakery

.. _docs/DESIGN.md: docs/DESIGN.md
.. _pi: https://github.com/earendil-works/pi
