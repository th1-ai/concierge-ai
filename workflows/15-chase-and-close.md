# Workflow: chasing, closing the loop, and the day-of check

Objective: keep every booked request moving without a person having to
remember it - chase a vendor who has gone quiet, close the loop once they
confirm, and run the final day-of check for transport.

## Chasing an unanswered vendor

```bash
make run ARGS=""     # or directly:
python3 tools/chase.py --once
```

`tools/chase.py` looks at every request `awaiting_vendor` whose chase task is
due (`config/agent.yaml`'s `chase.interval_hours`, default 24h) and drafts one
nudge, queued for review like any other message. After
`chase.max_follow_ups` (default 3) unanswered chases, the task escalates
itself and a `concierge_escalation` item appears in the queue instead of
another nudge - a person takes it from there. Schedule this daily
(`config/agent.yaml`'s `schedule.chase`).

## When a vendor replies

If the reply arrives on the same email thread the outreach was sent on
(`<ref>-vendor`), `tools/run.py`'s next pass logs it automatically and, when
`rules.confirm_both_sides` is on (the default), drafts both the vendor
acknowledgement and the guest confirmation straight away - vendor first, then
the guest, matching the promise that both sides always hear back.

If it arrived some other way (a phone call, a reply forwarded from a personal
inbox) or `confirm_both_sides` is off, close it yourself:

```bash
python3 tools/request.py log-reply REQ-1234 --text "Confirmed, driver Mário, plate 12-AB-34"
python3 tools/request.py close-loop REQ-1234          # uses the last logged reply
python3 tools/request.py close-loop REQ-1234 --vendor-reply "..."   # or pass it directly
```

`close-loop` always drafts both messages, regardless of the rule - it is a
human deciding to close it now.

## Guest budget sign-off

When an estimate is over `rules.budget_cap_eur`, the guest is asked before
anything is drafted for the vendor. Their reply, if it arrives on the same
WhatsApp chat, is read automatically for a clear yes/no; anything ambiguous is
left in the thread for you:

```bash
python3 tools/request.py guest-approved REQ-1234
python3 tools/request.py guest-declined REQ-1234 --text "Guest said the price was too high"
```

## The day-of check (transport only)

Run this each morning (`config/agent.yaml`'s `schedule.dayof`):

```bash
python3 tools/request.py dayof-sweep
```

Drafts one check-in message per `confirmed` transport request, asking the
vendor to confirm the driver is on schedule (with the flight number, when
there is one). Once they reply:

```bash
python3 tools/request.py dayof-replied REQ-1234 --text "On schedule, driver already en route"
```

drafts the final "you're all set" message to the guest and closes the request
(`pipeline_status: done`).

## Rules

- Every step here only ever queues a draft. `workflows/80-review.md` is the
  only place anything gets sent.
- `close-loop`, `guest-approved`/`-declined`, `log-reply` and
  `dayof-replied` all refuse to act on a request that is not in the right
  `pipeline_status` - the error message says which status it expected.
