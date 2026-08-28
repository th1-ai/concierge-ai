"""Tests for tools/run.py's intake loop against the bundled fixtures, with
provider=mock. No network, no credentials - this is what `make demo` runs.
"""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import load_settings
from core.store import Store
from tools import review, store_ext as sx
from tools.run import one_pass


def _settings():
    return load_settings(provider="mock", mode="shadow")


def test_one_pass_processes_every_fixture(tmp_path):
    store = Store(_settings(), path=tmp_path / "a.db")
    sx.ensure_schema(store)
    code, stats = one_pass(_settings(), store, limit=50, provider="mock")
    assert code == 0
    assert stats["processed"] == 6  # 5 emails + 1 whatsapp message
    store.close()


def test_the_flagship_chef_request_books_and_self_confirms(tmp_path):
    """REQ-1001: matched, under budget, and its scripted vendor reply arrives
    in the SAME fixture batch, so the loop closes itself in one pass."""
    store = Store(_settings(), path=tmp_path / "b.db")
    sx.ensure_schema(store)
    one_pass(_settings(), store, limit=50, provider="mock")
    req = sx.get_by_ref(store, "REQ-1001")
    assert req is not None
    assert req.vendor_key == "chef"
    assert req.estimate_total == 380
    assert req.pipeline_status == "confirmed"
    kinds = {i.kind for i in store.list_items(kind=None, limit=100)
            if (i.payload or {}).get("ref") == "REQ-1001"}
    assert {"concierge_guest_ack", "concierge_vendor_outreach", "concierge_vendor_ack",
           "concierge_guest_confirmation"} <= kinds


def test_no_vetted_vendor_escalates_and_sends_nothing(tmp_path):
    store = Store(_settings(), path=tmp_path / "c.db")
    sx.ensure_schema(store)
    one_pass(_settings(), store, limit=50, provider="mock")
    req = sx.get_by_ref(store, "REQ-1002")
    assert req is not None and req.pipeline_status == "escalated"
    item = store.get_by_external("concierge", "REQ-1002:escalation")
    assert item is not None
    assert item.review_status == "needs_human"
    assert item.draft is None  # nothing was drafted to send


def test_flipping_trusted_only_off_uses_the_unvetted_fallback(tmp_path, monkeypatch):
    import tools.run as run_mod
    settings = _settings()
    monkeypatch.setattr(settings, "agent",
                       {**settings.agent, "rules": {**settings.agent.get("rules", {}),
                                                    "vendor_trusted_only": False}})
    store = Store(settings, path=tmp_path / "d.db")
    sx.ensure_schema(store)
    vendors, fallbacks = run_mod._vendors_config(settings)
    outcome = run_mod._new_request(
        settings, store, vendors, fallbacks, source="email", external_id="x1",
        category="leisure", details="a sunset sailing charter, for 4 of us",
        guest_name="Test Guest", room_number="100", guest_email="test@example.com",
        guest_chat_id="", phone="", country="")
    req = sx.list_requests(store, limit=1)[0]
    assert req.vendor_unvetted is True
    assert "unvetted" in req.vendor_name
    assert outcome in ("booked", "budget_confirm")  # 4 x €145 = €580, over the €500 cap


def test_shadow_mode_never_sends_anything(tmp_path):
    store = Store(_settings(), path=tmp_path / "e.db")
    sx.ensure_schema(store)
    one_pass(_settings(), store, limit=50, provider="mock")
    counts = store.counts()
    assert counts.get("sent", 0) == 0
    assert counts.get("auto_sent", 0) == 0


def test_rerun_is_idempotent(tmp_path):
    store = Store(_settings(), path=tmp_path / "f.db")
    sx.ensure_schema(store)
    one_pass(_settings(), store, limit=50, provider="mock")
    first_count = len(sx.list_requests(store, limit=100))
    code, stats = one_pass(_settings(), store, limit=50, provider="mock")
    assert code == 0
    assert stats["skipped"] == 6  # every fixture was already seen
    assert len(sx.list_requests(store, limit=100)) == first_count
    store.close()


def test_transport_request_computes_the_child_seat_formula(tmp_path):
    store = Store(_settings(), path=tmp_path / "g.db")
    sx.ensure_schema(store)
    one_pass(_settings(), store, limit=50, provider="mock")
    req = sx.get_by_ref(store, "REQ-1003")
    assert req is not None
    assert req.child_seats == 2
    assert req.estimate_total == 115
    assert req.flight_number == "ZZ 1758"


def test_whatsapp_music_request_is_translated_into_french(tmp_path):
    store = Store(_settings(), path=tmp_path / "h.db")
    sx.ensure_schema(store)
    one_pass(_settings(), store, limit=50, provider="mock")
    req = sx.get_by_ref(store, "REQ-1005")
    assert req is not None and req.language == "fr"
    item = store.get_by_external("concierge", "REQ-1005:guest_ack")
    assert item is not None
    assert "Merci" in (item.draft or {}).get("body", "")


def test_retry_after_translate_pended_reuses_the_same_ref(tmp_path, monkeypatch):
    """`llm.provider: interactive` can park the guest-ack translation. The
    retry must resume REQ-1005 (WhatsApp, French), not mint a second ref for
    the same inbound message - see tools/store_ext.get_by_source_external."""
    import tools.engine as engine_mod
    from core.llm import LLMPendingInteractive

    settings = _settings()
    store = Store(settings, path=tmp_path / "i.db")
    sx.ensure_schema(store)

    real_translate = engine_mod.translate_draft
    pended = {"done": False}

    def pending_once(*a, **kw):
        # Only the WhatsApp French request (REQ-1005) ever actually calls the
        # model - every other fixture is English and short-circuits inside
        # translate_draft before this stand-in would even matter.
        if kw.get("fixture_id") == "REQ-1005-guest-ack" and not pended["done"]:
            pended["done"] = True
            raise LLMPendingInteractive("p1", tmp_path / "p1.prompt.md", None,
                                        tmp_path / "p1.answer.json")
        return real_translate(*a, **kw)

    monkeypatch.setattr(engine_mod, "translate_draft", pending_once)
    import tools.run as run_mod
    monkeypatch.setattr(run_mod, "engine", engine_mod)

    from tools.run import one_pass
    code, _ = one_pass(settings, store, limit=50, provider="mock")
    assert code == 3  # parked, exactly like tools/run.py's own docstring says

    reqs_after_pause = sx.list_requests(store, limit=50)
    assert any(r.ref == "REQ-1005" for r in reqs_after_pause)
    assert len([r for r in reqs_after_pause if r.category == "occasion"]) == 1

    code, stats = one_pass(settings, store, limit=50, provider="mock")
    assert code == 0
    reqs_after_retry = [r for r in sx.list_requests(store, limit=50) if r.category == "occasion"]
    assert len(reqs_after_retry) == 1  # still exactly one - no second ref was minted
    assert reqs_after_retry[0].ref == "REQ-1005"
    item = store.get_by_external("concierge", "REQ-1005:guest_ack")
    assert item is not None and item.review_status == "pending_review"


def test_dry_run_writes_nothing_even_with_two_new_requests_in_one_pass(tmp_path):
    """SIMULATION.md BLOCKER #2: two brand-new requests in the same pass used
    to collide - both peek the same un-incremented ref
    (core.store.Store.next_sequence(dry_run=True) never burns a number) and
    the second INSERT hit sqlite3.IntegrityError on the UNIQUE ref column.
    `--dry-run` must compute and print, but write nothing - not the request
    row, not the queue item, not a task - so nothing is there to collide on.
    Run it twice, on the same (still empty) database, to prove it never
    burns state between runs either."""
    settings = load_settings(provider="mock", mode="shadow", dry_run=True)
    store = Store(settings, path=tmp_path / "dry.db")
    sx.ensure_schema(store)
    for _ in range(2):
        code, stats = one_pass(settings, store, limit=50, provider="mock")
        assert code == 0
        assert stats["processed"] == 6  # every fixture computed fresh, nothing marked seen
        assert sx.list_requests(store, limit=100) == []  # no concierge_requests row, ever
        assert store.counts() == {}  # no review-queue item either
    store.close()


def test_guest_language_outside_hotel_languages_falls_back_and_needs_human(tmp_path, monkeypatch):
    """SIMULATION.md MAJOR #3: a guest writing in a language the hotel does
    not read (here, German - Hotel Aurora's config/hotel.yaml only speaks
    en/pt/es/fr) must get a draft in the hotel's default language, and the
    item must be `needs_human` with a reason a person can act on - never a
    silent, unreviewable-by-staff draft in the guest's own language."""
    import tools.run as run_mod

    settings = _settings()
    store = Store(settings, path=tmp_path / "lang.db")
    sx.ensure_schema(store)
    vendors, fallbacks = run_mod._vendors_config(settings)
    german_details = ("Guten Tag, wir haetten gerne einen privaten Koch fuer unser "
                      "Abendessen im Zimmer. Vielen Dank und mit freundlichen Gruessen.")

    outcome = run_mod._new_request(
        settings, store, vendors, fallbacks, source="email", external_id="lang-test-1",
        category="dining", details=german_details, guest_name="Guest", room_number="101",
        guest_email="guest@example.com", guest_chat_id="", phone="", country="")

    assert outcome == "booked"  # a vetted chef vendor still matches on category
    req = sx.list_requests(store, limit=1)[0]
    assert req.language in settings.hotel.languages  # never drafts in "de"
    assert req.language == settings.hotel.default_language
    assert req.language_note == "guest wrote in de, not in hotel.languages"

    item = store.get_by_external("concierge", f"{req.ref}:guest_ack")
    assert item is not None
    assert item.review_status == "needs_human"
    assert item.payload.get("reason") == "guest wrote in de, not in hotel.languages"
    # The agent's own wording must be in English (the hotel's default), not
    # translated into the guest's German - staff must be able to check it.
    # The template still quotes the guest's original request verbatim for
    # context, so this checks the agent's own sentences, not the whole body.
    body = (item.draft or {}).get("body", "")
    assert "Thank you for your request" in body
    assert "Concierge desk, Hotel Aurora" in body


def test_sample_item_shows_marker_in_list_line_and_show(tmp_path, capsys):
    """core/store.py tags an item read through a mock adapter outside `make
    demo` as `_sample` (`Item.is_sample`) - a human working the real queue
    must see that at a glance, in both `list` and `show`."""
    settings = _settings()
    store = Store(settings, path=tmp_path / "sample.db")
    sx.ensure_schema(store)
    item = store.upsert_item("email", "sample-marker-1", kind="concierge_vendor_outreach",
                             payload={"ref": "REQ-9001", "_sample": True})
    assert item.is_sample

    capsys.readouterr()
    review._print_item_line(item)
    assert "[SAMPLE DATA]" in capsys.readouterr().out

    rc = review.cmd_show(store, SimpleNamespace(id=item.id))
    assert rc == 0
    assert "[SAMPLE DATA]" in capsys.readouterr().out
    store.close()
