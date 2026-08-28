# Workflow: the weekly coach pass

Objective: turn the edits and rejections staff made in the review queue this
week into concrete fixes, without ever sending anything to a guest itself.

This is the Email Optimizer / Coach AI layer (`email-coach-ai` in the roster;
it applies to Concierge AI, Front Desk AI, Upsell AI and CRM / Lead Nurture
AI). Off by default - turn it on in `config/agent.yaml`'s `coach: enabled:
true` once you have a few weeks of real review-queue activity to learn from.

## Why this looks different here

The coach's roster promise is "read every reply a human edited, rejected, or
thumbed-down... applies the safe knowledge-base fixes itself, and proposes
the rest." Concierge AI's booking decisions are deterministic Python
(`tools/engine.py` - no LLM touches them, see `docs/how-it-works.md`), so
there is no prompt for a knowledge-base fix to change. Most accepted
suggestions here are really "edit `config/vendors.yaml`" or "edit
`config/agent.yaml`'s rules" - a human still makes that change, but the coach
tells you exactly what pattern to fix and how many times it happened.

## Steps

1. **Cluster this period's edits.**
   ```bash
   python3 tools/coach.py run
   ```
   Groups everything in `core.store`'s `learnings` table (written by
   `core/review.py:edit()`/`reject()` every time a human changes or discards
   a draft) by what kind of message it was. A cluster of 2 or more gets ONE
   suggestion from the model (`prompts/coach-suggestion.md`) - specific,
   never a generic "improve the prompt".

2. **See what it found.**
   ```bash
   python3 tools/coach.py list
   ```

3. **Decide, per proposal.**
   ```bash
   python3 tools/coach.py accept <id>     # appends it to knowledge/rules.md
   python3 tools/coach.py reject <id> --reason "..."
   ```
   Nothing is ever auto-applied - the roster's "applies the safe fixes
   itself" is deliberately not implemented here; every fix waits for a human
   nod (see `specs/email-coach-ai.md` section 11, the open question this
   closes conservatively). `accept` only writes to `knowledge/rules.md`,
   which `prompts/translate.md` reads - if the fix is really a
   `config/vendors.yaml` change, make that edit yourself; the rules file is
   the durable record of the decision either way.

4. **Watch the trend.** `make report` shows the human-edit rate
   (`docs/benefits.md`) - the roster's own measure of an agent "earning its
   autonomy" as that rate falls.

## Rules

- The coach never talks to a guest or a vendor - it only ever reads
  `learnings` and writes proposals + `knowledge/rules.md`.
- Schedule this weekly (`config/agent.yaml`'s `schedule.coach`), not more
  often - a cluster needs a real week of review-queue activity to mean
  anything.
