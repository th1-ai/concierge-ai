# Workflow: working the review queue

Objective: turn a queued draft into a decision - approve, edit, or reject -
and, once approved, actually send it.

Nothing reaches a guest or a vendor without going through this. `mode: shadow`
blocks every send, full stop - approved or not, edited or not. Approving,
editing or rejecting a draft only ever records your decision; nothing leaves
until the hotel is in `mode: live` (`workflows/90-go-live.md`). See
`docs/safety.md` for the full guard.

## Steps

1. **See what is waiting.**
   ```bash
   make review
   ```
   Each line shows the item id, status, kind (`concierge_guest_ack`,
   `concierge_vendor_outreach`, `concierge_vendor_ack`,
   `concierge_guest_confirmation`, `concierge_vendor_chase`,
   `concierge_dayof_check`, `concierge_guest_dayof_update`,
   `concierge_pms_note`, or `concierge_escalation`), the request's `ref`, and
   the subject.

2. **Read one in full.**
   ```bash
   python3 tools/review.py show <id>
   ```
   Prints the draft and the full event history. Summarise it in plain
   language - who wrote in, what Concierge AI drafted, why - do not paste raw
   JSON at the hotel.

3. **Decide.**
   ```bash
   python3 tools/review.py approve <id>
   python3 tools/review.py edit <id> --body-file my-version.txt [--subject "..."]
   python3 tools/review.py reject <id> --reason "wrong vendor"
   ```
   `edit` records the before/after pair - that is what the coach layer reads
   (`workflows/85-coach-weekly.md`). `concierge_escalation` items have no
   draft to approve; read the reason, act on it outside the tool (call the
   guest, find another supplier), then `reject <id> --reason "handled: ..."`
   to clear it from the queue.

4. **Send what was approved.**
   ```bash
   python3 tools/review.py send
   ```
   Claims everything `approved`/`edited` and sends it through the right
   channel: email or WhatsApp for guest- and vendor-facing drafts, or
   `pms.add_note()` for the internal booking note (`concierge_pms_note`) -
   the item's own `payload.channel` decides. In `mode: shadow` this sends
   nothing at all, even an item you just approved - you will see
   `blocked ...: mode is shadow: the approval is recorded, but nothing
   leaves in shadow mode` for every item. That is expected, not a bug: the
   approval is recorded either way, it just does not go anywhere until the
   hotel is in `mode: live`.

5. **A failed send.** `send` marks the item `failed` with the error attached.
   In `mode: shadow` that error is always the shadow block above - expected,
   not a problem to fix. In `mode: live` it usually means a mailbox or
   vendor-email typo (`make doctor` says which).
   ```bash
   python3 tools/review.py retry <id>
   ```
   re-queues it once the cause is fixed.

## Rules

- Only `tools/review.py` writes `approved` / `edited` / `rejected`.
- A vendor outreach, guest confirmation and PMS note can all be sitting in the
  queue for the same request at once - review each on its own, they do not
  have to be approved together.
- Confirm with the hotel before sending anything, even an approved item, the
  first few times. `workflows/90-go-live.md` covers when to stop doing that.
