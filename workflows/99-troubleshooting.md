# Workflow: troubleshooting

Read the whole error before doing anything - every tool here is written to
say what broke and what to do about it. If you fix something not covered
below, add it here.

## `make doctor` shows a FAIL

Each `FAIL` line has a `->` fix hint right under it. Common ones:

- **`hotel identity`: name is still 'Hotel Aurora'.** Expected on a fresh
  clone. Edit `config/hotel.yaml`.
- **`vendor book`: no vendors in config/vendors.yaml.** Run
  `cp config/vendors.example.yaml config/vendors.yaml` (or re-run
  `make setup`) and fill in your real suppliers.
- **`concierge rules`: rules.* missing from config/agent.yaml.** Copy
  `config/agent.example.yaml` to `config/agent.yaml`.
- **`llm provider`: claude-code selected but `claude` is not on PATH.**
  Install Claude Code, or switch `llm.provider` to `interactive` or
  `anthropic`.
- **An adapter shows FAIL, not warn.** `universal`/`built` adapters fail
  loud when misconfigured (`warn` is reserved for stubs). The `detail`
  column names the missing file or variable.

## `make demo` does not print `DEMO OK`

- Make sure `make setup` ran first (`.venv` must exist).
- `tools/demo.py` forces `llm.provider=mock` and reads
  `fixtures/inbound/*.json` and `fixtures/hotel/*.json` - if you deleted or
  renamed those, restore them from git.
- Read the traceback if there is one; `tools/demo.py` does not swallow
  errors on purpose.

## `make run` exits with code 3

Not an error. `llm.provider: interactive` parked a translation prompt (the
only automatic model call this agent makes). Read `data/pending/*.prompt.md`,
write your answer to the matching `*.answer.json` (JSON only, matching the
schema shown), and run the same command again.

**Scripting or cron around this:** `make` itself always reports its own exit
code (2) when a recipe fails, whatever the underlying tool actually
returned - so a wrapper around `make run` cannot tell "waiting on an answer"
(3) apart from a real error (1). Call `tools/run.py` directly instead, the
same way `make schedule` generates its snippets:
```bash
.venv/bin/python tools/run.py --once
echo $?   # 0 ok, 3 waiting on an interactive answer, 1 a real error
```

## A request is stuck at `escalated` and I don't know why

```bash
python3 tools/request.py show <ref>
```
shows the parsed request and, in the matching `concierge_escalation` item's
payload, exactly which rule fired (`python3 tools/review.py show <id>`). It is
almost always "no vetted vendor for this category" - check
`config/vendors.yaml`'s keywords, or turn `rules.vendor_trusted_only` off in
`config/agent.yaml` to see the unvetted-fallback path.

## The vendor never replies and the chase keeps firing

That is `tools/chase.py` working as designed - after
`chase.max_follow_ups` (default 3) unanswered nudges it stops chasing on its
own and raises a `concierge_escalation` instead. If a vendor's real turnaround
time is longer than `chase.interval_hours`, raise it in `config/agent.yaml`.

## An item is stuck at `sending`

A process died between claiming an item and finishing the send.
`tools/run.py` calls `core.store.Store.reap_stuck_sending()` on every pass,
moving anything stuck for more than 30 minutes to `failed`. Use
`python3 tools/review.py retry <id>` once the cause is fixed.

## The vendor's reply did not get picked up automatically

Automatic detection only matches a reply on the exact thread id the outreach
was sent on (`docs/how-it-works.md`). A reply that arrived some other way -
forwarded from a personal inbox, a phone call - needs
`python3 tools/request.py log-reply <ref> --text "..."` or
`python3 tools/request.py close-loop <ref> --vendor-reply "..."`.

## `python3 tools/*.py` says `ModuleNotFoundError: No module named 'core'`

You ran it with a Python that is not the repo's virtualenv, or from outside
the repo root. Use `make run` / `make doctor` / etc. (they call
`.venv/bin/python` for you), or run `.venv/bin/python tools/run.py` directly
from the repo root.

## Still stuck

`data/logs/*.jsonl` has every decision, in order, with a run id.
`python3 tools/review.py show <id>` has the full event trail for one item.
`python3 tools/request.py show <ref>` has the full trail for one request. If
none of that explains it, that is a real bug - describe exactly what you ran
and what you expected, and ask.
