# Guardrails and safety

This agent talks to your guests and touches your systems. Everything below is
built in, not optional, and this page explains what it does and what is left for
you to decide.

## The two modes

| Mode | What happens |
|---|---|
| `shadow` (default) | The agent reads, thinks, drafts and queues. It **never** sends a message and **never** writes to your PMS. Approving, editing or rejecting a draft records your decision (and teaches the coach layer) but sends nothing - `python3 tools/review.py send` blocks every item, approved or not, with the reason `mode is shadow`. At go-live, `python3 tools/review.py stale` clears that shadow-era queue so nothing old goes out by surprise. |
| `live` | Items you approved are really sent. Everything else still waits. |

`mode` lives in `config/hotel.yaml`. It is a global kill switch: flipping it back
to `shadow` stops every outbound action immediately, mid-schedule, with no other
change. `config/agent.yaml` can be stricter than `hotel.yaml`, never looser.

Two more brakes:

- `make run ARGS="--dry-run"` computes everything and writes nothing, even in
  live mode. Use it when you change a prompt.
- `review.require_approval_for` in `config/hotel.yaml` lists the actions that
  need a human even in live mode. The defaults are `send_email`, `send_message`,
  `pms_write`, `payment`, `publish`. Shortening that list is how you hand the
  agent more rope, one action at a time.

Every outbound action in the codebase goes through one function,
`core/review.py:assert_write_allowed`. There is no second path.

## The review queue

Nothing reaches a guest without passing through the queue.

```bash
make review                       # what is waiting
python3 tools/review.py show <id>  # the full draft and how it got there
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file my-version.txt
python3 tools/review.py reject <id> --reason "wrong tone"
```

An item moves `new -> pending_review` (or `needs_human` for an escalation)
and then waits. Only `tools/review.py` can write `approved`, `edited` or
`rejected`; only `tools/review.py send` can write `sent`. A crash between
"about to send" and "sent" is picked up on the next pass and shown to you as
failed rather than silently retried.

**Your edits teach it.** When you rewrite a draft, the before and after are
stored. Over time that is what makes the drafts sound like your hotel instead of
like a machine.

## What the agent will not do

- Send anything while `mode: shadow`.
- Send an item a human has not approved, when the action needs approval.
- Take a payment, issue a refund, or move money. Payment adapters are read-only
  by design.
- Invent a fact that is not in `knowledge/` or in the data it was given. When it
  is not sure, it queues the item as `needs_human` instead of guessing.
- Argue. Complaints, refund requests, legal or medical topics, and anything that
  reads as distressed go straight to a person.
- **Invent a vendor.** `tools/engine.py:match_vendor()` can only ever return a
  supplier listed in `config/vendors.yaml`. No vetted match, with
  `rules.vendor_trusted_only` on (the default), means an escalation - nothing
  is drafted or sent to anyone, ever, for that request.
- **Contact a vendor before a large spend is signed off.** Above
  `rules.budget_cap_eur`, the guest is asked first (`pipeline_status:
  awaiting_guest_budget`) and the vendor hears nothing until they say yes.
- **Charge anything.** `pms.add_note()` logs a confirmed booking for a human
  to post to the folio by hand - no adapter in this family can charge a card.
- **Chase forever.** `tools/chase.py` stops after `chase.max_follow_ups`
  unanswered nudges and raises an escalation instead - see
  `workflows/15-chase-and-close.md`.
- **Guess at a vendor's or a guest's reply.** A message on a known thread is
  logged and surfaced to a human; the agent never infers "confirmed" from
  free text on its own (see `docs/how-it-works.md`'s "Detecting a reply").
- **Reply in a language nobody on your team can check.** `tools/run.py`
  drafts in the guest's own language only when it is one of
  `config/hotel.yaml`'s `hotel.languages`; otherwise it drafts in the
  hotel's default language and queues the item `needs_human` with the
  reason "guest wrote in \<lang\>, not in hotel.languages" - so a person
  who can actually read the wording checks it before it goes anywhere.

## The trusted-vendor guardrail, provably

This is the headline guardrail (roster `cant`: "Escalates only the genuinely
bespoke: unknown providers..."). To see it work:

1. Send a request for something not in `config/vendors.yaml` (a helicopter
   tour, say). `make run` escalates it - `needs_human`, no draft, nothing
   sent.
2. Set `rules.vendor_trusted_only: false` in `config/agent.yaml` and run it
   again. It now drafts to the unvetted fallback in `config/vendors.yaml`,
   and every draft it produces says "(unvetted)" in the vendor's name, so a
   human reviewing it can never mistake it for a vetted booking.
3. Set the rule back to `true` before you go live.

## Data handling

**What leaves your machine.** With `llm.provider: anthropic` or `claude-code`,
the prompt goes to Anthropic. That prompt contains the guest message and the
relevant property facts. With `llm.provider: mock` or `interactive`, nothing
leaves the machine at all.

**What is stored, and where.** Everything lives in `data/` inside this folder:
`agent.db` (SQLite), `logs/*.jsonl`, `exports/`. `data/` is gitignored. There is
no cloud service behind this repo and no telemetry.

**Card numbers are redacted on the way in.** Every inbound message passes through
`core/redact.py` before it is stored, logged or put into a prompt. A payment card
number is replaced with `[CARD REDACTED ****1234]`, and labelled CVC and expiry
values in the same message go with it. Detection requires a real card prefix and
a valid Luhn checksum, so booking references and door codes survive. IBANs are
masked the same way. Nothing you can do in config turns this off.

**Retention.** `privacy.retention_days` (default 365) is how long processed items
stay in the database. Deleting `data/agent.db` deletes everything the agent knows.

## GDPR, in practice

If you are in the EU or handle EU guests' data, the short version:

- **You are the controller.** This software runs on your machine, under your
  control, on your data. TH1 does not receive it.
- **Your model provider is a processor.** If you use the `anthropic` or
  `claude-code` provider, Anthropic processes guest data on your behalf. Check
  their data processing terms and record them in your processing register.
- **Purpose and minimisation.** The agent sees the message and the property facts
  it needs. Do not put staff phone numbers, card data or full guest histories in
  `knowledge/`.
- **Right to erasure.** A guest asking to be deleted means removing their rows
  from `data/agent.db` and any exported CSVs. Ask your Claude session:
  *"Delete every item in data/agent.db whose payload mentions this email address,
  and tell me how many rows you removed."*
- **Retention.** Set `privacy.retention_days` to what your own policy says, not
  to the default.

This is a practical summary, not legal advice.

## Telling guests they are talking to AI

The EU AI Act (Article 50) requires that a person is told when they are
interacting with an AI system, unless it is obvious. Whether it applies to you
depends on where you and your guests are, but it is good practice everywhere and
guests react well to it.

`knowledge/signature.example.md` has suggested wording:

> This message was prepared with AI assistance and reviewed by our team. Reply
> any time to reach a person directly.

It is not wired into every draft automatically in this template - every
message a hotel actually sends goes through the review queue first, so add
the line where it fits your own mailbox's outgoing signature, or ask your
Claude session to add it to `tools/engine.py`'s `_signature()` helper for
every draft at once. Keep the escape hatch in the sentence. A guest who wants
a human should never have to work out how to get one.

## Subscription or API: an honest note

Two ways to pay for the reasoning:

**Your Claude Code subscription** (`llm.provider: claude-code` or `interactive`).
Flat monthly cost, no per-message billing. This is genuinely the cheapest way to
run a small hotel's agent.

The caveat, plainly: a personal Pro or Max subscription is intended for
interactive use, and Anthropic's usage policy and rate limits apply to automated
use of it. A handful of scheduled runs a day is a normal way to work. Pointing
a busy inbox at it around the clock is not, and you will hit rate limits at the
worst moment. Read the terms and decide for yourself.

**The Anthropic API** (`llm.provider: anthropic`). Pay per token, no ambiguity
about automated use, proper rate limits, and usage you can attribute. This is
the right answer for production volume. `make report` shows what you are
spending.

Start on the subscription while you are learning what the agent does. Move to the
API when it becomes part of how the hotel runs.

## If something goes wrong

1. `mode: shadow` in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env`. Every
   outbound action stops on the next pass.
2. Remove the schedule (`crontab -e`, `launchctl unload`, or
   `systemctl disable --now <slug>.timer`).
3. `make doctor` to see what the agent thinks its state is.
4. `data/logs/*.jsonl` has every decision, with the run id, in order.
