"""Tests for tools/coach.py - the Email Optimizer / Coach AI layer.

Never touches the real knowledge/rules.md - RULES_PATH is monkeypatched to a
temp file, same as every other write in this suite stays inside tmp_path.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.review import edit, reject
from core.store import Store
from tools import coach


def _settings():
    return load_settings(provider="mock", mode="shadow")


def _seed_learnings(store, n: int, kind: str = "concierge_guest_ack"):
    for i in range(n):
        item = store.upsert_item("email", f"seed-{kind}-{i}", kind=kind,
                                 payload={"ref": f"REQ-{9100 + i}"})
        store.transition(item.id, "pending_review")
        store.set_fields(item.id, draft={"subject": "s", "body": "original"})
        edit(store, item.id, {"subject": "s", "body": "edited version"}, note="tone")


def test_no_proposal_below_the_cluster_threshold(tmp_path):
    store = Store(_settings(), path=tmp_path / "a.db")
    store.migrate(coach.COACH_SCHEMA)
    _seed_learnings(store, 1)
    coach.cmd_run(_settings(), store, argparse_ns())
    rows = store.db.execute("SELECT * FROM coach_proposals").fetchall()
    assert len(rows) == 0


def test_a_cluster_of_edits_produces_one_proposal(tmp_path):
    store = Store(_settings(), path=tmp_path / "b.db")
    store.migrate(coach.COACH_SCHEMA)
    _seed_learnings(store, 3)
    coach.cmd_run(_settings(), store, argparse_ns())
    rows = store.db.execute("SELECT * FROM coach_proposals WHERE status='pending'").fetchall()
    assert len(rows) == 1
    assert rows[0]["count"] == 3


def test_running_twice_does_not_duplicate_a_pending_proposal(tmp_path):
    store = Store(_settings(), path=tmp_path / "c.db")
    store.migrate(coach.COACH_SCHEMA)
    _seed_learnings(store, 3)
    coach.cmd_run(_settings(), store, argparse_ns())
    coach.cmd_run(_settings(), store, argparse_ns())
    rows = store.db.execute("SELECT * FROM coach_proposals").fetchall()
    assert len(rows) == 1


def test_accept_appends_to_rules_and_never_touches_the_real_repo(tmp_path, monkeypatch):
    fake_rules = tmp_path / "knowledge" / "rules.md"
    monkeypatch.setattr(coach, "RULES_PATH", fake_rules)
    store = Store(_settings(), path=tmp_path / "d.db")
    store.migrate(coach.COACH_SCHEMA)
    _seed_learnings(store, 2)
    coach.cmd_run(_settings(), store, argparse_ns())
    proposal_id = store.db.execute(
        "SELECT id FROM coach_proposals WHERE status='pending'").fetchone()["id"]
    coach.cmd_accept(store, proposal_id)
    assert fake_rules.exists()
    row = store.db.execute("SELECT status FROM coach_proposals WHERE id=?",
                           (proposal_id,)).fetchone()
    assert row["status"] == "accepted"


def test_reject_marks_the_proposal_rejected_and_writes_nothing(tmp_path, monkeypatch):
    fake_rules = tmp_path / "knowledge" / "rules.md"
    monkeypatch.setattr(coach, "RULES_PATH", fake_rules)
    store = Store(_settings(), path=tmp_path / "e.db")
    store.migrate(coach.COACH_SCHEMA)
    _seed_learnings(store, 2)
    coach.cmd_run(_settings(), store, argparse_ns())
    proposal_id = store.db.execute(
        "SELECT id FROM coach_proposals WHERE status='pending'").fetchone()["id"]
    coach.cmd_reject(store, proposal_id, "not useful")
    assert not fake_rules.exists()


def test_the_coach_never_writes_to_guests_or_vendors(tmp_path):
    """cant: 'Doesn't talk to guests.' - the coach's own store must show 0 sends."""
    store = Store(_settings(), path=tmp_path / "f.db")
    store.migrate(coach.COACH_SCHEMA)
    _seed_learnings(store, 3)
    coach.cmd_run(_settings(), store, argparse_ns())
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0


class argparse_ns:  # noqa: N801 - tiny stand-in, cmd_run does not read any field of it
    pass
