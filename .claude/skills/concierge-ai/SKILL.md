---
name: concierge-ai
description: Run Concierge AI ("The Fixer") — Handles concierge requests end-to-end: books restaurant tables, private chefs, and airport/VIP transfers on the guest's behalf.. Use when the user asks to run the agent, check what is waiting for review, approve or reject a draft, or asks how the agent is doing. Trigger phrases: "run The Fixer", "/concierge-ai", "check the queue", "what is waiting for me", "approve that draft".
---

# Concierge AI

Runs Concierge AI and works its review queue. Everything happens from the repo
root; every command below exists and works.

## Before anything else

Read `README.md` if you have not this session, and `workflows/10-*.md` for the
main loop. If the user has never run this agent, start at `workflows/00-setup.md`
instead and walk them through it.

## The loop

**1. Check the agent is healthy.**

```bash
make doctor
```

Any `FAIL` line has a fix hint. Fix it before going further. `WARN` lines are
worth mentioning but do not stop the run.

**2. Run one pass.**

```bash
make run                        # one pass over new work
make run ARGS="--limit 5"       # just the first five items
make run ARGS="--dry-run"       # compute everything, write nothing
```

If `llm.provider` is `interactive`, the run will stop with exit code 3 and park
prompts in `data/pending/`. That is expected. Read each `*.prompt.md`, write your
answer as JSON to the matching `*.answer.json` following the schema exactly, then
run the same command again.

**2b. Check the pipeline, chase, and close loops.**

```bash
python3 tools/request.py list                # every request, by pipeline_status
python3 tools/chase.py --once                # nudge vendors who have gone quiet
python3 tools/request.py close-loop <ref>     # once a vendor has confirmed
python3 tools/request.py dayof-sweep          # today's transport check-ins
```

A phone call or a front-desk conversation has no adapter - log it with
`python3 tools/request.py add --category ... --details "..." --guest-name "..." --room ...`.

**3. Show what is waiting.**

```bash
make review
python3 tools/review.py show <id>
```

Summarise it for the user in plain language: who wrote in, what the agent thinks
it is, what it drafted, and how confident it was. Do not paste raw JSON at them.

**4. Act on their decision.**

```bash
python3 tools/review.py approve <id>
python3 tools/review.py edit <id> --body-file <path>
python3 tools/review.py reject <id> --reason "<why>"
```

Read the draft back to them before approving. If they want changes, write the new
version to a file and use `edit` — the before/after is stored and is what teaches
the agent their voice.

**5. Report.**

```bash
make report
```

## Rules

- **Never send in shadow mode**, and never work around a blocked write. The error
  message says what to do.
- **Going live is the hotel's decision.** Only raise it after
  `workflows/90-go-live.md` has been worked through.
- **Confirm before anything irreversible** — a guest email, a vendor email, a
  PMS note — even when it is approved.
- **No vetted vendor means an escalation, not an invented supplier.** If
  `python3 tools/request.py list --status escalated` shows something, read the
  reason and act on it outside the tool; do not turn off
  `rules.vendor_trusted_only` without the hotel's say-so.
- **Never print or paste a credential.**
- If a run fails, read the whole error, fix the cause, re-run, and note what you
  learned in `workflows/99-troubleshooting.md`.
