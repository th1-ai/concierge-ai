#!/usr/bin/env python3
"""tools/demo.py - the whole loop on the bundled fixtures, zero credentials.

    make demo
    python3 tools/demo.py

Forces `llm.provider=mock`, `mode=shadow`, and the `mock` adapter for every
system (`core.config.load_settings(demo=True)`) regardless of what
config/hotel.yaml has configured, so this always works on a fresh clone with
a blank .env - even after a hotel has pointed `systems.email.adapter` at
`gmail` or `imap`. Runs against its own database (`data/demo/demo.db`), so
running it twice always shows the same results, and never touches
`data/agent.db` (that is `make run`'s file).

One pass over the fixtures shows every branch: a booked request that gets
confirmed within the same run (the vendor's reply is a second fixture on the
same thread), an escalation (no vetted vendor), two more booked requests
awaiting a vendor reply, and a WhatsApp request in French that comes back
translated.
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

import store_ext as sx  # noqa: E402
from core.config import ConfigError, load_settings, sub_data_dir  # noqa: E402
from core.log import summary_line  # noqa: E402
from core.store import Store  # noqa: E402
from run import one_pass  # noqa: E402


def main() -> int:
    try:
        settings = load_settings(demo=True)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    demo_db = sub_data_dir("demo") / "demo.db"
    if demo_db.exists():
        demo_db.unlink()
    store = Store(settings, path=demo_db)
    sx.ensure_schema(store)

    print("Concierge AI demo - 6 sample inbound items from fixtures/inbound/\n")
    code, stats = one_pass(settings, store, limit=50, provider="mock")
    if code != 0:
        print("error: the demo pass did not complete cleanly", file=sys.stderr)
        return 1

    for r in sx.list_requests(store, limit=50):
        vendor = r.vendor_name or "(escalated — no vetted vendor)"
        line = f"  {r.ref}: \"{r.category}\" -> {vendor}  pipeline={r.pipeline_status}"
        if r.estimate_line:
            line += f"  ({r.estimate_line})"
        print(line)

    print(f"\n{stats['escalated']} escalated, {stats['drafted']} new draft(s) queued for "
         f"review, {stats['replies']} repl(y/ies) logged.")
    print("Nothing was sent: mode is shadow, and demo never calls send() at all.")
    print("Next: `make review` to see the drafts, or read workflows/10-new-requests.md.\n")

    print(f"DEMO OK — {summary_line(stats, settings.mode)}")
    store.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
