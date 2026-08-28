# Workflow: shadow to live

Objective: decide, with the hotel, whether Concierge AI is ready to send
approved messages on its own instead of only drafting them - and make the
change safely if so.

This is the hotel's decision, never yours. Do not suggest it until the
checklist below is genuinely true, and when you do raise it, say plainly what
changes.

## Checklist

- [ ] `make doctor` is clean (no `FAIL` lines). `warn` on `mode` is expected
      until you flip it.
- [ ] `config/hotel.yaml` has the real property name, address and contact
      details.
- [ ] `config/vendors.yaml` has real vendors with real contact emails and
      real rates - not the shipped Hotel Aurora examples.
- [ ] At least a few days of real requests have gone through the review
      queue, not just the demo fixtures - guest acks, vendor outreach, at
      least one closed loop.
- [ ] The hotel has read enough drafts across all the message kinds
      (guest ack, vendor outreach, vendor ack, guest confirmation, chase
      nudge, day-of check) to trust the wording. Nothing here is drafted by a
      model - see `docs/how-it-works.md` - so this is really a check on the
      templates in `tools/engine.py` and the vendor data in
      `config/vendors.yaml`, not on model quality.
- [ ] The hotel has decided on, and added, the AI-disclosure line for any
      guest-facing message (`docs/safety.md` has suggested wording and the
      EU AI Act Article 50 context).
- [ ] A real mailbox and, if used, WhatsApp are connected
      (`systems.email.adapter` / `systems.messaging.adapter`) and
      `make doctor` shows them healthy.
- [ ] The hotel understands `rules.vendor_trusted_only` stays on - going live
      does not change the guardrail; it changes who is allowed to press send.

## Making the change

1. Clear the shadow-era backlog so none of it goes out by surprise the
   moment `send` stops being blocked:
   ```bash
   python3 tools/review.py stale
   ```
   Everything still sitting un-sent from testing is marked `stale` instead
   of queued to send. Nothing here sends anything - it only relabels items,
   the same as approve/edit/reject.
2. Edit `config/hotel.yaml`:
   ```yaml
   mode: live
   ```
3. `review.require_approval_for` still lists `send_email` and `send_message`
   by default - it should. Going live means **approved drafts get sent**, not
   that Concierge AI starts sending unapproved ones.
4. Run `make doctor` again to confirm.
5. Run one real pass and manually watch a send go through:
   ```bash
   make run ARGS="--limit 1"
   python3 tools/review.py list
   python3 tools/review.py approve <id>
   python3 tools/review.py send
   ```
6. Tell the hotel exactly what just changed: an approved draft now really
   leaves the mailbox the next time someone (or a scheduled job) runs
   `python3 tools/review.py send` - it is still never automatic before that
   approval.

## Going back to shadow

```yaml
mode: shadow
```
in `config/hotel.yaml`, or `AGENT_MODE=shadow` in `.env` for one run. Either
stops every outbound action on the next pass, mid-schedule, with no other
change required.
