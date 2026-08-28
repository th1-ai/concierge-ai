# The business case

## What the roster promises

**Output:** "Runs the full book → confirm → follow-up loop autonomously;
turns a multi-touch ~15-min-per-request chore into zero-touch, and captures
concierge revenue usually lost to \"we'll get to it.\""

**ROI:** −90% on "Staff time per concierge request" (labor).

## The problem this solves

A concierge request - a private chef, a transfer, tickets, live music - is
high-delight and high-effort at the same time: someone has to parse what the
guest actually wants, know which supplier to call, chase them, and remember
to tell the guest once it's confirmed. It is exactly the kind of multi-touch
job that gets dropped when the desk is busy, and every drop is a guest who
notices.

## What to measure

```bash
make report
```

reads straight from `data/agent.db` and shows:

- **Requests handled and their pipeline breakdown** - how many are new,
  awaiting a vendor, confirmed, done, or escalated.
- **Escalation rate** - the share with no vetted vendor. A rising rate is a
  sign `config/vendors.yaml` needs a new entry, not a sign the agent is
  failing; a falling rate as you add vendors is the guardrail earning its
  keep.
- **Average touches per request** (confirmed/done only) - the mean number of
  thread entries per closed request. This is the roster's own "multi-touch
  ~15-min chore" made concrete: fewer touches on the record is less staff
  time per request, even before anyone times it with a stopwatch.
- **Human-edit rate** - the share of approved drafts a person rewrote before
  sending, from `core/review.py:edit()`. Falling over time is the signal
  that the vendor book and templates match how this property actually talks
  to guests and suppliers.
- **LLM spend** - `core.llm.complete()` records usage and cost to the
  `events` table on every call. Since translating a guest-facing draft is
  the only automatic model call this agent makes (`docs/how-it-works.md`),
  this is a small, predictable number even at volume - it is zero on
  `mock`/`interactive`. `make report --narrate` adds one more call, opt-in.

## Honest caveats

- The −90% figure is the roster's claim for the agent operated as designed:
  approvals happening promptly, `config/vendors.yaml` filled in with real
  suppliers, and the review queue actually worked (`workflows/80-review.md`).
  An unfilled vendor book just produces escalations, which cost as much
  staff time as doing it by hand.
- "Zero-touch" describes the agent's own work - parsing, matching,
  estimating, drafting, chasing. A human still has to approve every send;
  that time is real and does not disappear, though it is far less than
  drafting from scratch and remembering to follow up.
- The day-of check only exists for transport, matching the spec exactly -
  see `docs/how-it-works.md`'s design decisions. A dining or tickets booking
  does not get a day-of check in this template.
- No integration writes a real charge to the guest's folio - `pms.add_note()`
  logs the booking for a person to post by hand. "€X on your folio" in a
  guest confirmation is a statement of intent, not a completed charge.
