---
description: Distill recent daily notes into MEMORY.md
---

It is time to consolidate your memory, following your memory rules.

1. Read `MEMORY.md` and the daily notes since its last consolidation, in
   `$BAKERY_MEMORY_DIR` (`rg` there helps find related entries).
2. If a `<changes>` feed follows, it lists the operator's commits to the
   bakery checkout you run from: your instructions, tools, sandbox, and the
   gateway and its collectors. For each line in `MEMORY.md` or the notes
   that one of them could have made wrong (eg. what the sandbox or a tool
   allows, how scout or the task list behaves, where something lives),
   recheck it now with your tools, reading the commit when it helps.
3. Update `MEMORY.md` with `memory_edit`: add durable facts, decisions, and
   lessons; recheck or remove lines dated more than 14 days ago; correct or
   remove entries that are now wrong, including any step 2 could not
   verify; merge duplicates. Keep it concise and organized by topic, not by
   date. Record the date you consolidated up to at the top, as
   `Consolidated through: YYYY-MM-DD`.
4. Never delete or edit the daily notes themselves.

If nothing needs to change, reply with exactly NO_REPLY. Otherwise reply with a
short summary of what changed (and which commits caused it), followed by a list
of `external` notes you held back, if any.
