#!/usr/bin/env python3
"""tools/report.py - what Concierge AI did, and what it cost.

    make report
    python3 tools/report.py
    python3 tools/report.py --since 2026-09-01
    python3 tools/report.py --narrate

Reads straight from `data/agent.db` - no export step, no external service.
See docs/benefits.md for what each number is meant to show and why.

`--narrate` is the only place in this whole agent that calls a model outside
`tools/run.py`'s translation step, and it is opt-in on purpose: a one-line
AI summary of the numbers below, via `tools/engine.py:narrate()`. Skip it
(the default) and this command never touches `llm.provider` at all.
"""

from __future__ import annotations

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
from core.llm import LLMPendingInteractive  # noqa: E402
from core.store import Store, StoreError  # noqa: E402


def _edit_rate(store: Store, since: str | None) -> tuple[int, int, float]:
    sql = "SELECT action, COUNT(*) AS n FROM events WHERE action IN ('status:approved','status:edited')"
    params: list = []
    if since:
        sql += " AND ts >= ?"
        params.append(since)
    sql += " GROUP BY action"
    counts = {r["action"]: r["n"] for r in store.db.execute(sql, params).fetchall()}
    approved = counts.get("status:approved", 0)
    edited = counts.get("status:edited", 0)
    total = approved + edited
    return approved, edited, (edited / total * 100 if total else 0.0)


def _touches(requests) -> float:
    done = [r for r in requests if r.pipeline_status in ("confirmed", "done")]
    if not done:
        return 0.0
    return sum(len(r.thread) for r in done) / len(done)


def main(argv: list[str] | None = None) -> int:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--since", default=None, help="ISO date/time, e.g. 2026-09-01")
    parser.add_argument("--narrate", action="store_true",
                        help="add a one-line AI summary (one model call)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    sx.ensure_schema(store)
    try:
        requests = sx.list_requests(store, limit=10_000)
        counts = store.counts()
        queue = {"waiting_on_human": sum(counts.get(s, 0) for s in
                                        ("pending_review", "needs_human", "stale", "failed")),
                "sent": counts.get("sent", 0) + counts.get("auto_sent", 0)}
        pipeline: dict[str, int] = {}
        for r in requests:
            pipeline[r.pipeline_status] = pipeline.get(r.pipeline_status, 0) + 1
        escalated = pipeline.get("escalated", 0)
        total = len(requests) or 1
        approved, edited, edit_pct = _edit_rate(store, args.since)
        usage = store.usage_totals(since=args.since)

        print(f"Concierge AI report{f' (since {args.since})' if args.since else ''}\n")
        print(f"  Requests handled:        {len(requests)}")
        for status in sx.PIPELINE_STATUSES:
            if pipeline.get(status):
                print(f"    {status:<22} {pipeline[status]}")
        print(f"  Escalation rate:         {escalated}/{total} ({escalated/total*100:.1f}%)")
        print(f"  Avg touches per request: {_touches(requests):.1f} (confirmed/done only)")
        print(f"  Waiting on a human:      {queue['waiting_on_human']}")
        print(f"  Sent:                    {queue['sent']}")
        print(f"  Human-edit rate:         {edited}/{approved + edited} approvals ({edit_pct:.1f}%)")
        print(f"  LLM calls:               {usage['calls']} "
             f"({usage['input_tokens']} in / {usage['output_tokens']} out tokens)")
        print(f"  LLM spend:               ${usage['cost_usd']:.4f} "
             "(0 unless llm.provider is anthropic or claude-code)")

        if args.narrate:
            summary = {"requests": len(requests), "escalated": escalated,
                      "confirmed": pipeline.get("confirmed", 0) + pipeline.get("done", 0),
                      "edit_rate_pct": round(edit_pct, 1)}
            try:
                note = engine.narrate(settings, summary, store=store)
            except LLMPendingInteractive as exc:
                print(str(exc))
                return 3
            if note:
                print(f"\n  AI summary:              {note}")
        return 0
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
