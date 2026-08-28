#!/usr/bin/env python3
"""tools/chase.py - the follow-up sweep: nudge vendors who have gone quiet.

    python3 tools/chase.py --once
    python3 tools/chase.py --once --dry-run

Ports the source system's `concierge-followup-sweep` cron. For every open
`vendor_chase` task past its due date (`core.store`'s tasks table -
`config/agent.yaml`'s `chase.interval_hours` sets the gap), this drafts one
nudge to the vendor and queues it for review - it never sends anything on its
own, same as every other draft in this repo. `core.store.advance_task` moves
the due date forward and, once `chase.max_follow_ups` is reached, flips the
task to `escalated` automatically - at that point this tool raises a
`concierge_escalation` item instead of drafting another nudge, so a human
takes over rather than the agent chasing forever.

Run this daily (`config/agent.yaml`'s `schedule.chase`) alongside `make run`.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import engine  # noqa: E402
import store_ext as sx  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.log import Run, get_logger  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

log = get_logger("chase")


def sweep(settings, store) -> dict:
    """``settings.dry_run`` (``--dry-run``) computes and prints what the sweep
    would do - which tasks are due, which would escalate - but writes
    nothing: no task advance, no escalation, no chase drafted, no request
    status change. Same rule as ``tools/run.py``."""
    stats = {"chased": 0, "escalated": 0}
    dry_run = settings.dry_run
    with Run("chase", settings, store) as run:
        for task in store.due_tasks(kind="vendor_chase"):
            request = sx.get_request(store, task.ref_id)
            if request is None or request.pipeline_status != "awaiting_vendor":
                if not dry_run:
                    store.close_task(task.id, status="stale")
                continue

            follow_up_count = task.follow_up_count + 1
            gap_days = max(1, int(settings.agent_get("chase.interval_hours", 24)) // 24) or 1
            # Mirrors core.store.Store.advance_task()'s own escalation check -
            # against the task's OWN max_follow_ups (fixed when the task was
            # created), not a fresh re-read of config/agent.yaml, in case the
            # hotel has changed chase.max_follow_ups since.
            will_escalate = follow_up_count >= task.max_follow_ups
            if not dry_run:
                advanced = store.advance_task(
                    task.id, gap_days=gap_days,
                    note=f"chase #{follow_up_count} for {request.ref}")
                will_escalate = advanced.status == "escalated"

            if will_escalate:
                if not dry_run:
                    store.upsert_item(
                        "concierge", f"{request.ref}:escalation", kind="concierge_escalation",
                        payload={"ref": request.ref,
                                "reason": f"no reply from {request.vendor_name} after "
                                         f"{follow_up_count} chases"})
                    item = store.get_by_external("concierge", f"{request.ref}:escalation")
                    if item is not None and item.review_status == "new":
                        store.transition(item.id, "needs_human", actor="agent")
                    sx.update_request(store, request.id, pipeline_status="escalated")
                stats["escalated"] += 1
                log.info("chases exhausted, escalating", ref=request.ref)
                continue

            nudge = engine.build_vendor_chase(hotel_name=settings.hotel.name, ref=request.ref,
                                              vendor_name=request.vendor_name,
                                              follow_up_count=follow_up_count)
            if not dry_run:
                item = store.upsert_item(
                    "concierge", f"{request.ref}:chase:{follow_up_count}",
                    kind="concierge_vendor_chase",
                    payload={"ref": request.ref, "channel": "email", "to": request.vendor_contact,
                            "thread_id": request.vendor_thread_id})
                if item.review_status == "new":
                    store.set_fields(item.id, draft=nudge)
                    store.transition(item.id, "pending_review", actor="agent")
            stats["chased"] += 1
            log.info("drafted a chase", ref=request.ref, attempt=follow_up_count)

        run.stats = dict(stats)
    return stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--once", action="store_true", help="run the sweep once (default)")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing")
    parser.add_argument("--provider", default=None)
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    sx.ensure_schema(store)
    try:
        stats = sweep(settings, store)
        print(f"CHASE OK — {stats['chased']} chased, {stats['escalated']} escalated "
             f"({settings.mode})")
        return 0
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
