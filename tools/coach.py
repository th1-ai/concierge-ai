#!/usr/bin/env python3
"""tools/coach.py - the Email Optimizer / Coach AI layer for this agent.

    python3 tools/coach.py run       # cluster this week's edits, propose fixes
    python3 tools/coach.py list      # pending proposals
    python3 tools/coach.py accept <id>
    python3 tools/coach.py reject <id> --reason "..."

The spec's coach ("The Mentor") reads every edit or rejection a human makes,
clusters the corrections into patterns, applies the safe knowledge-base fixes
itself, and proposes the rest. This template does the reading and clustering
and writes ONE suggestion per pattern (`prompts/coach-suggestion.md`), but
never auto-applies anything - every proposal waits for `accept`, which is the
conservative reading of the spec's own open question ("nothing is auto-
applied is the single biggest gap between promise and behavior" -
`specs/email-coach-ai.md` section 11).

Concierge AI's booking decisions are deterministic (`tools/engine.py`, no LLM
touches them - see docs/how-it-works.md), so most accepted fixes are really
instructions for a human to edit `config/vendors.yaml` or `config/agent.yaml`
directly. `accept` appends the suggestion as a line in `knowledge/rules.md`
as the durable record of that decision; `prompts/translate.md` also reads
that file, so an accepted phrasing/tone fix carries into every language.

Off by default - `config/agent.yaml`'s `coach.enabled`. Turning it on costs
nothing extra on `llm.provider: mock`/`interactive`; it uses one model call
per NEW cluster of edits, not per edit.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive, complete  # noqa: E402
from core.store import Store, StoreError, utcnow  # noqa: E402
from core.templates import build_prompt  # noqa: E402

SCHEMA_PATH = REPO_ROOT / "prompts" / "schemas" / "coach.json"
RULES_PATH = REPO_ROOT / "knowledge" / "rules.md"
MIN_CLUSTER = 2

COACH_SCHEMA = """
CREATE TABLE IF NOT EXISTS coach_proposals (
  id           TEXT PRIMARY KEY,
  cluster_key  TEXT NOT NULL,
  count        INTEGER NOT NULL,
  suggestion   TEXT NOT NULL,
  status       TEXT NOT NULL DEFAULT 'pending',
  created_at   TEXT NOT NULL,
  decided_at   TEXT
);
"""


def _clip(text: str, limit: int = 200) -> str:
    """Shorten long examples on a word boundary, with a visible ellipsis."""
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[:limit].rsplit(" ", 1)[0] + " [...]"


def _schema() -> dict:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def cmd_run(settings, store, args) -> int:
    learnings = store.list_learnings(limit=500)
    clusters: dict[str, list[dict]] = defaultdict(list)
    for row in learnings:
        clusters[row["applied_to"] or "unclassified"].append(row)

    proposed = 0
    for key, rows in clusters.items():
        if len(rows) < MIN_CLUSTER:
            continue
        already = store.db.execute(
            "SELECT id FROM coach_proposals WHERE cluster_key=? AND status='pending'",
            (key,)).fetchone()
        if already:
            continue
        examples = "\n".join(
            f"- before: \"{_clip(r['before'] or '(none)')}\" -> "
            f"after: \"{_clip(r['after'] or '(rejected, no replacement)')}\" "
            f"({r['lesson']})" for r in rows[:5])
        prompt = build_prompt("coach-suggestion", settings=settings,
                              applied_to=key, count=len(rows), examples=examples,
                              fixture_id=f"coach-{key}")
        try:
            result = complete("coach-suggestion", prompt, _schema(), settings=settings,
                              store=store, fixture_id=f"coach-{key}")
            suggestion = (result.data or {}).get("suggestion", "").strip()
        except LLMPendingInteractive:
            raise  # let main() report it and exit 3 - same convention as tools/run.py
        except LLMError as exc:
            print(f"skipped '{key}': {exc}", file=sys.stderr)
            continue
        if not suggestion:
            continue
        store.db.execute(
            "INSERT INTO coach_proposals (id, cluster_key, count, suggestion, status, "
            "created_at) VALUES (?,?,?,?,?,?)",
            (uuid.uuid4().hex, key, len(rows), suggestion, "pending", utcnow()))
        proposed += 1
        print(f"proposed for '{key}' ({len(rows)} edits): {suggestion}")

    print(f"\nCOACH OK — {proposed} new proposal(s) from {len(learnings)} recorded edit(s).")
    return 0


def cmd_list(store) -> int:
    rows = store.db.execute(
        "SELECT * FROM coach_proposals WHERE status='pending' ORDER BY created_at ASC"
    ).fetchall()
    if not rows:
        print("No proposals are waiting for a decision.")
        return 0
    for row in rows:
        print(f"  {row['id']}  ({row['cluster_key']}, {row['count']} edits)\n"
             f"    {row['suggestion']}")
    return 0


def cmd_accept(store, proposal_id: str) -> int:
    row = store.db.execute("SELECT * FROM coach_proposals WHERE id=?",
                           (proposal_id,)).fetchone()
    if row is None:
        print(f"error: no proposal {proposal_id}", file=sys.stderr)
        return 1
    RULES_PATH.parent.mkdir(parents=True, exist_ok=True)
    if not RULES_PATH.exists():
        RULES_PATH.write_text(
            "# Rules learned from the review queue\n\n"
            "One line per accepted coach proposal. Every prompt in this repo reads this "
            "file as part of its system block - see core/templates.py.\n\n", encoding="utf-8")
    with RULES_PATH.open("a", encoding="utf-8") as fh:
        fh.write(f"- {row['suggestion']}\n")
    store.db.execute("UPDATE coach_proposals SET status='accepted', decided_at=? WHERE id=?",
                     (utcnow(), proposal_id))
    print(f"accepted: appended to {RULES_PATH}")
    return 0


def cmd_reject(store, proposal_id: str, reason: str) -> int:
    row = store.db.execute("SELECT id FROM coach_proposals WHERE id=?",
                           (proposal_id,)).fetchone()
    if row is None:
        print(f"error: no proposal {proposal_id}", file=sys.stderr)
        return 1
    store.db.execute("UPDATE coach_proposals SET status='rejected', decided_at=? WHERE id=?",
                     (utcnow(), proposal_id))
    print(f"rejected{f' ({reason})' if reason else ''}.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help="cluster edits/rejections and propose fixes")
    sub.add_parser("list", help="proposals waiting for a decision")
    p_accept = sub.add_parser("accept", help="write the suggestion to knowledge/rules.md")
    p_accept.add_argument("id")
    p_reject = sub.add_parser("reject", help="discard the proposal")
    p_reject.add_argument("id")
    p_reject.add_argument("--reason", default="")
    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    store.migrate(COACH_SCHEMA)
    try:
        if args.command == "run":
            try:
                return cmd_run(settings, store, args)
            except LLMPendingInteractive as exc:
                print(str(exc))
                return 3
        if args.command == "list":
            return cmd_list(store)
        if args.command == "accept":
            return cmd_accept(store, args.id)
        if args.command == "reject":
            return cmd_reject(store, args.id, args.reason)
        parser.error(f"unknown command {args.command}")
        return 2
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
