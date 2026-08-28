"""Tests for tools/chase.py - the follow-up sweep."""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store, utcnow
from tools import store_ext as sx
from tools.chase import sweep
from tools.run import _draft_and_queue_vendor_outreach


def _settings():
    return load_settings(provider="mock", mode="shadow")


def _make_awaiting_vendor_request(store):
    request = sx.create_request(
        store, ref="REQ-9001", category="dining", details="a private chef for 4",
        source="email", guest_email="guest@example.com", party=4,
        pipeline_status="new")
    vendor = {"key": "chef", "name": "Chef Ines Duarte (vetted)", "channel": "email",
             "contact": "chef@example.com", "unit_label": "per_cover", "unit_price_eur": 95}
    request = sx.update_request(store, request.id, vendor_key="chef", vendor_name=vendor["name"],
                                vendor_contact=vendor["contact"], estimate_total=380,
                                estimate_line="4 x €95 per cover = €380")
    _draft_and_queue_vendor_outreach(_settings(), store, request, vendor)
    return sx.get_by_ref(store, "REQ-9001")


def test_sweep_does_nothing_before_the_due_date(tmp_path):
    store = Store(_settings(), path=tmp_path / "a.db")
    sx.ensure_schema(store)
    _make_awaiting_vendor_request(store)
    stats = sweep(_settings(), store)
    assert stats == {"chased": 0, "escalated": 0}


def test_sweep_drafts_a_nudge_once_the_task_is_due(tmp_path):
    store = Store(_settings(), path=tmp_path / "b.db")
    sx.ensure_schema(store)
    request = _make_awaiting_vendor_request(store)
    store.db.execute("UPDATE tasks SET next_action_due=? WHERE ref_id=?",
                     ("2000-01-01T00:00:00+00:00", request.id))
    stats = sweep(_settings(), store)
    assert stats["chased"] == 1
    item = store.get_by_external("concierge", "REQ-9001:chase:1")
    assert item is not None and item.review_status == "pending_review"
    assert "REQ-9001" in (item.draft or {}).get("subject", "")


def test_sweep_escalates_after_max_follow_ups(tmp_path):
    store = Store(_settings(), path=tmp_path / "c.db")
    sx.ensure_schema(store)
    request = _make_awaiting_vendor_request(store)
    store.db.execute(
        "UPDATE tasks SET next_action_due=?, follow_up_count=?, max_follow_ups=3 WHERE ref_id=?",
        ("2000-01-01T00:00:00+00:00", 2, request.id))  # the 3rd chase hits the cap
    stats = sweep(_settings(), store)
    assert stats["escalated"] == 1
    updated = sx.get_by_ref(store, "REQ-9001")
    assert updated.pipeline_status == "escalated"
    item = store.get_by_external("concierge", "REQ-9001:escalation")
    assert item is not None and item.review_status == "needs_human"


def test_sweep_never_sends_a_chase_it_only_drafts_it(tmp_path):
    store = Store(_settings(), path=tmp_path / "d.db")
    sx.ensure_schema(store)
    request = _make_awaiting_vendor_request(store)
    store.db.execute("UPDATE tasks SET next_action_due=? WHERE ref_id=?", (utcnow(), request.id))
    sweep(_settings(), store)
    counts = store.counts()
    assert counts.get("sent", 0) == 0


def test_sweep_dry_run_writes_nothing(tmp_path):
    """Same `--dry-run` rule as tools/run.py: compute and report, write
    nothing - not the chase draft, not the task's follow_up_count."""
    settings = load_settings(provider="mock", mode="shadow", dry_run=True)
    store = Store(settings, path=tmp_path / "e.db")
    sx.ensure_schema(store)
    request = _make_awaiting_vendor_request(store)
    store.db.execute("UPDATE tasks SET next_action_due=? WHERE ref_id=?",
                     ("2000-01-01T00:00:00+00:00", request.id))
    stats = sweep(settings, store)
    assert stats == {"chased": 1, "escalated": 0}  # still computed and reported
    assert store.get_by_external("concierge", "REQ-9001:chase:1") is None  # never written
    tasks = store.due_tasks(kind="vendor_chase")
    assert len(tasks) == 1 and tasks[0].follow_up_count == 0  # task itself untouched
