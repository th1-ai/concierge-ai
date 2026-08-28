# Workflow: new requests

Objective: turn every inbound concierge request - email, WhatsApp, or one you
type in from a phone call or the front desk - into a drafted guest
acknowledgement and vendor outreach (or an honest escalation), ready for a
human to approve.

## Inputs

- `systems.email.adapter` and `systems.messaging.adapter` in
  `config/hotel.yaml` (`mock` by default - `workflows/00-setup.md` step 6
  covers connecting real ones).
- `config/vendors.yaml` - the vetted supplier book.
- `config/agent.yaml`'s `rules:` block (`vendor_trusted_only`,
  `budget_cap_eur`, `confirm_both_sides`).

## Steps

1. **Run one pass.**
   ```bash
   make run
   make run ARGS="--limit 5"       # just the first five items
   make run ARGS="--dry-run"       # compute everything, write nothing
   ```
   Every unread email and every new WhatsApp message is either a brand-new
   request or a reply on a thread the agent is already chasing - see
   `docs/how-it-works.md` for how it tells the two apart. A new request is
   parsed (party size, timing, dietary, child seats - `tools/engine.py`),
   matched against `config/vendors.yaml`, estimated, and budget-checked, all
   without a model call.

2. **A phone call or a front-desk conversation** has no adapter - type it in:
   ```bash
   python3 tools/request.py add --category dining \
     --details "Private chef for 4 on Saturday, one pescatarian" \
     --guest-name "Guest name" --room 214
   ```
   This runs exactly the same parse/match/estimate/draft logic as an email.

3. **See what happened.**
   ```bash
   python3 tools/request.py list
   python3 tools/request.py show REQ-1234
   ```
   `pipeline_status` tells you where each request stands: `new` ->
   `awaiting_guest_budget` (over the cap - a human is asked before the vendor
   is contacted) or straight to `awaiting_vendor` -> `confirmed` -> `done`.
   `escalated` means no vetted vendor matched - nothing was sent to anyone.

4. **Work the queue.** `workflows/80-review.md` covers approve / edit /
   reject / send in full.

5. **Keep it running.**
   ```bash
   make watch                       # loop on the configured interval
   ```
   Or schedule it - `make schedule` and `scheduler/` have cron, launchd and
   systemd examples. `config/agent.yaml`'s `schedule.new_requests` documents
   the interval this repo was built around (every 15 minutes). The follow-up
   sweep and the day-of check are separate workflows -
   `workflows/15-chase-and-close.md`.

## Edge cases

- **No new mail or messages.** `make run` prints `0 items processed` and
  exits 0. Nothing to do.
- **A guest's own words don't state a party size.** The agent assumes 2 and
  says so plainly in the request record (`party_source`) rather than
  guessing higher or lower.
- **No vetted vendor matches.** With `rules.vendor_trusted_only: true`
  (the default), the request is escalated: `needs_human`, no draft, nothing
  sent to the guest or a vendor. Turn the rule off in `config/agent.yaml` and
  re-run to see the unvetted-fallback path instead - every draft it produces
  says "(unvetted)" in the vendor name.
- **The estimate is over `rules.budget_cap_eur`.** The guest is asked to sign
  off before the vendor hears anything - see `python3 tools/request.py
  guest-approved`/`guest-declined` in `workflows/15-chase-and-close.md`.
- **A re-run sees the same email or message again.** `tools/run.py` skips
  anything the store has already seen - see
  `core.store.Store.already_processed`.
