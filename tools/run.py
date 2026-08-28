#!/usr/bin/env python3
"""tools/run.py - Concierge AI's intake loop: fetch -> parse -> match -> draft.

    python3 tools/run.py --once
    python3 tools/run.py --watch
    python3 tools/run.py --once --dry-run
    python3 tools/run.py --once --limit 5 --provider mock

One pass does three things, in order:

1. Fetch unread email and new WhatsApp messages. Anything on a thread this
   agent is already chasing (see ``vendor_thread_id``) is a VENDOR REPLY, not
   a new request - it is logged, its chase task is closed, and (when
   ``rules.confirm_both_sides`` is on) the vendor-ack + guest-confirmation
   drafts are queued straight away. Everything else is a brand-new request.
2. A new request is parsed, matched against a vetted vendor, estimated and
   budget-checked (``tools/engine.py`` - all deterministic), then drafted:
   a guest acknowledgement plus either a vendor outreach, a guest budget
   sign-off ask, or an escalation.
3. Every draft is queued in the review FSM (``core.store``). Nothing is ever
   sent from here - see ``workflows/80-review.md``.

Exit codes: 0 ok, 3 waiting on an `interactive` answer, 1 a real error.
"""

from __future__ import annotations

import argparse
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import engine  # noqa: E402
import store_ext as sx  # noqa: E402
from core.adapters import get_email, get_messaging, get_pms  # noqa: E402
from core.adapters.base import AdapterError  # noqa: E402
from core.config import ConfigError, load_settings, load_yaml  # noqa: E402
from core.i18n import detect_language  # noqa: E402
from core.llm import LLMError, LLMPendingInteractive  # noqa: E402
from core.log import Run, get_logger, summary_line  # noqa: E402
from core.review import WriteBlocked  # noqa: E402
from core.store import Store, StoreError  # noqa: E402

log = get_logger("run")


def _vendors_config(settings) -> tuple[list[dict], list[dict]]:
    raw = load_yaml("vendors")
    return list(raw.get("vendors") or []), list(raw.get("unvetted_fallbacks") or [])


def _queue(settings, store, *, source: str, external_id: str, kind: str, draft: dict | None,
          payload: dict, status: str) -> tuple[object | None, bool]:
    """Idempotent: a second pass over the same (source, external_id) is a no-op.

    ``settings.dry_run`` skips every write here - not the upsert, not the
    draft, not the transition - and returns ``(None, True)``. Nothing in
    this repo reads the returned item back out of a dry-run call."""
    if settings.dry_run:
        return None, True
    item = store.upsert_item(source, external_id, kind=kind, payload=payload)
    if item.review_status != "new":
        return item, False
    if draft is not None:
        store.set_fields(item.id, draft=draft)
    updated = store.transition(item.id, status, actor="agent")
    return updated, True


def _handle_vendor_reply(settings, store, request, reply_text: str, *, force: bool = False,
                         log_it: bool = True) -> None:
    """``force=True`` is ``tools/request.py close-loop``: a human closing the
    loop themselves, regardless of ``rules.confirm_both_sides``."""
    if log_it:
        sx.append_thread(store, request.id, role="vendor", text=reply_text,
                         dry_run=settings.dry_run)
    if not settings.dry_run:
        task_rows = store.db.execute(
            "SELECT id FROM tasks WHERE kind='vendor_chase' AND ref_id=? AND status='open'",
            (request.id,)).fetchall()
        for row in task_rows:
            store.close_task(row["id"], status="done")

    confirm_both = force or bool(settings.agent_get("rules.confirm_both_sides", True))
    if not confirm_both:
        log.info("vendor replied, confirm_both_sides is off - waiting for a human",
                 ref=request.ref)
        return

    estimate = engine.Estimate(request.estimate_total, request.estimate_line)
    vendor_ack = engine.build_vendor_ack(hotel_name=settings.hotel.name, ref=request.ref,
                                        vendor_reply_text=reply_text)
    guest_confirmation = engine.build_guest_confirmation(
        hotel_name=settings.hotel.name, ref=request.ref, vendor_name=request.vendor_name,
        estimate=estimate, vendor_reply_text=reply_text, currency=settings.hotel.currency)
    guest_confirmation = engine.translate_draft(
        settings, guest_confirmation, request.language, store=store,
        fixture_id=f"{request.ref}-guest-confirmation")
    _queue(settings, store, source="concierge", external_id=f"{request.ref}:vendor_ack", kind="concierge_vendor_ack",
          draft=vendor_ack, status="pending_review",
          payload={"ref": request.ref, "channel": "email", "to": request.vendor_contact})
    _queue(settings, store, source="concierge", external_id=f"{request.ref}:guest_confirmation",
          kind="concierge_guest_confirmation", draft=guest_confirmation, status="pending_review",
          payload={"ref": request.ref, "channel": "email" if request.guest_email else "messaging",
                   "to": request.guest_email or request.guest_chat_id})
    sx.update_request(store, request.id, pipeline_status="confirmed")
    log.info("vendor confirmed, closing the loop", ref=request.ref)

    if request.reservation_id:
        note = (f"Concierge: {request.category} booked with {request.vendor_name}, "
               f"{estimate.line}. See {request.ref}. No folio charge exists here yet - "
               "post it by hand.")
        _queue(settings, store, source="concierge", external_id=f"{request.ref}:pms_note",
              kind="concierge_pms_note", draft={"subject": "", "body": note},
              status="pending_review",
              payload={"ref": request.ref, "channel": "pms", "to": request.reservation_id})


def _handle_guest_budget_reply(settings, store, request, reply_text: str, approved: bool) -> None:
    sx.append_thread(store, request.id, role="guest", text=reply_text, dry_run=settings.dry_run)
    if not approved:
        sx.update_request(store, request.id, pipeline_status="done", dry_run=settings.dry_run)
        log.info("guest declined the spend", ref=request.ref)
        return
    vendors, _ = _vendors_config(settings)
    vendor = next((v for v in vendors if v.get("key") == request.vendor_key), None)
    if vendor is None:
        vendor = {"key": request.vendor_key, "name": request.vendor_name,
                 "channel": "email", "contact": request.vendor_contact,
                 "unit_label": request.unit_label, "unit_price_eur": request.unit_price_eur}
    _draft_and_queue_vendor_outreach(settings, store, request, vendor)


def _draft_and_queue_vendor_outreach(settings, store, request, vendor: dict) -> None:
    thread_id = f"{request.ref}-vendor"
    estimate = engine.Estimate(request.estimate_total, request.estimate_line)
    outreach = engine.build_vendor_outreach(
        hotel_name=settings.hotel.name, ref=request.ref, vendor=vendor, party=request.party,
        when_text=request.when_text, dietary=request.dietary, child_seats=request.child_seats,
        duration_text=request.duration_text, details=request.details, estimate=estimate,
        flight_number=request.flight_number)
    _queue(settings, store, source="concierge", external_id=f"{request.ref}:vendor_outreach",
          kind="concierge_vendor_outreach", draft=outreach, status="pending_review",
          payload={"ref": request.ref, "channel": "email", "to": vendor.get("contact", ""),
                   "thread_id": thread_id})
    if not settings.dry_run:
        interval_hours = int(settings.agent_get("chase.interval_hours", 24))
        due = (datetime.now(timezone.utc) + timedelta(hours=interval_hours)).isoformat(
            timespec="seconds")
        store.upsert_task("vendor_chase", request.id, next_action_due=due,
                          max_follow_ups=int(settings.agent_get("chase.max_follow_ups", 3)),
                          payload={"ref": request.ref})
    sx.update_request(store, request.id, pipeline_status="awaiting_vendor",
                      vendor_thread_id=thread_id, dry_run=settings.dry_run)


def _new_request(settings, store, vendors, fallbacks, *, source: str, external_id: str,
                 category: str, details: str, guest_name: str, room_number: str,
                 guest_email: str, guest_chat_id: str, phone: str, country: str,
                 reservation_id: str = "") -> str:
    """Parse, match, estimate, budget-check and draft ONE fresh request.
    Returns the outcome: 'escalated' | 'budget_confirm' | 'booked'.

    Idempotent across a pause: if ``translate_draft`` parks an ``interactive``
    prompt partway through (LLMPendingInteractive propagates up to
    ``one_pass``), the retry finds the request already created via
    ``store_ext.get_by_source_external`` and resumes with the SAME ``ref`` and
    vendor decision instead of parsing/matching/estimating again and minting
    a second one for the same inbound message."""
    existing = sx.get_by_source_external(store, source, external_id)
    if existing is not None:
        if existing.pipeline_status == "escalated":
            return "escalated"
        if existing.pipeline_status != "new":
            return existing.pipeline_status  # already past this point - nothing left to draft
        request = existing
        ref = existing.ref
        lang = existing.language
        language_note = existing.language_note
        vendor = {"key": existing.vendor_key, "name": existing.vendor_name,
                 "channel": "email", "contact": existing.vendor_contact,
                 "unit_label": existing.unit_label, "unit_price_eur": existing.unit_price_eur}
        estimate = engine.Estimate(existing.estimate_total, existing.estimate_line)
        over = bool(existing.over_budget)
    else:
        parsed = engine.parse_request(details)
        guess = detect_language(details, phone=phone, country=country, settings=settings)
        # Reply only in the hotel's languages: a guest writing in one the
        # hotel does not read (docs/safety.md, config/hotel.yaml's
        # `hotel.languages`) gets a draft in the hotel's default language
        # instead, and the item is queued `needs_human` so nobody approves
        # wording they cannot check - see docs/safety.md.
        supported = settings.hotel.languages or ["en"]
        if guess.lang in supported:
            lang, language_note = guess.lang, ""
        else:
            lang = settings.hotel.default_language
            language_note = f"guest wrote in {guess.lang}, not in hotel.languages"
            log.info("guest language not in hotel.languages - drafting in the "
                     "default and flagging needs_human", detected=guess.lang,
                     supported=supported)
        trusted_only = bool(settings.agent_get("rules.vendor_trusted_only", True))
        vendor = engine.match_vendor(category, details, vendors)
        unvetted = False

        if vendor is None and trusted_only:
            ref = sx.next_ref(store, dry_run=settings.dry_run)
            sx.create_request(
                store, dry_run=settings.dry_run, ref=ref, external_id=external_id,
                category=category, details=details,
                source=source, guest_name=guest_name, guest_email=guest_email,
                guest_chat_id=guest_chat_id, room_number=room_number, language=lang,
                language_note=language_note,
                party=parsed.party, party_source=parsed.party_source,
                child_seats=parsed.child_seats, when_text=parsed.when_text,
                dietary=parsed.dietary, duration_text=parsed.duration_text,
                flight_number=parsed.flight_number, reservation_id=reservation_id,
                pipeline_status="escalated")
            reason = engine.escalation_note(category=category, details=details)
            _queue(settings, store, source="concierge", external_id=f"{ref}:escalation",
                  kind="concierge_escalation", draft=None, status="needs_human",
                  payload={"ref": ref, "reason": reason})
            log.info("escalated: no vetted vendor", ref=ref, category=category)
            return "escalated"

        if vendor is None:
            vendor = engine.unvetted_fallback(category, details, fallbacks)
            unvetted = True

        estimate = engine.estimate_for(vendor, parsed.party, parsed.child_seats,
                                       parsed.duration_text, settings.hotel.currency)
        cap_raw = settings.agent_get("rules.budget_cap_eur", 500)
        cap = float(cap_raw) if cap_raw else 0.0
        over = engine.over_budget(estimate.total, cap, cap > 0)

        ref = sx.next_ref(store, dry_run=settings.dry_run)
        request = sx.create_request(
            store, dry_run=settings.dry_run, ref=ref, external_id=external_id,
            category=category, details=details,
            source=source, guest_name=guest_name, guest_email=guest_email,
            guest_chat_id=guest_chat_id, room_number=room_number, language=lang,
            language_note=language_note,
            party=parsed.party, party_source=parsed.party_source,
            child_seats=parsed.child_seats, when_text=parsed.when_text, dietary=parsed.dietary,
            duration_text=parsed.duration_text, flight_number=parsed.flight_number,
            reservation_id=reservation_id, vendor_key=vendor.get("key", ""),
            vendor_name=vendor.get("name", ""), vendor_contact=vendor.get("contact", ""),
            vendor_unvetted=int(unvetted), unit_label=vendor.get("unit_label", ""),
            unit_price_eur=vendor.get("unit_price_eur", 0), estimate_total=estimate.total,
            estimate_line=estimate.line, over_budget=int(over), pipeline_status="new")

    if over:
        cap_raw = settings.agent_get("rules.budget_cap_eur", 500)
        confirm = engine.build_budget_confirm(
            hotel_name=settings.hotel.name, ref=ref, details=details, estimate=estimate,
            cap=float(cap_raw) if cap_raw else 0.0, currency=settings.hotel.currency)
        confirm = engine.translate_draft(settings, confirm, lang, store=store,
                                         fixture_id=f"{ref}-budget-confirm")
        payload = {"ref": ref, "channel": "email" if guest_email else "messaging",
                  "to": guest_email or guest_chat_id}
        if language_note:
            payload["reason"] = language_note
        _queue(settings, store, source="concierge", external_id=f"{ref}:budget_confirm",
              kind="concierge_budget_confirm", draft=confirm,
              status="needs_human" if language_note else "pending_review", payload=payload)
        sx.update_request(store, request.id, pipeline_status="awaiting_guest_budget",
                          dry_run=settings.dry_run)
        log.info("over budget, asking the guest first", ref=ref, estimate=estimate.total)
        return "budget_confirm"

    ack = engine.build_guest_ack(hotel_name=settings.hotel.name, ref=ref, category=category,
                                 details=details, vendor_name=vendor.get("name"))
    ack = engine.translate_draft(settings, ack, lang, store=store,
                                 fixture_id=f"{ref}-guest-ack")
    ack_payload = {"ref": ref, "channel": "email" if guest_email else "messaging",
                  "to": guest_email or guest_chat_id}
    if language_note:
        ack_payload["reason"] = language_note
    _queue(settings, store, source="concierge", external_id=f"{ref}:guest_ack", kind="concierge_guest_ack",
          draft=ack, status="needs_human" if language_note else "pending_review",
          payload=ack_payload)
    _draft_and_queue_vendor_outreach(settings, store, request, vendor)
    log.info("booked path: drafted guest ack + vendor outreach", ref=ref,
            vendor=vendor.get("name"))
    return "booked"


_APPROVE_WORDS = ("yes", "confirm", "approved", "approve", "go ahead", "sounds good",
                  "okay", " ok ", "ok.", "ok,")
_DECLINE_WORDS = ("no", "cancel", "decline", "don't", "do not", "too much", "skip it")


def _guess_approval(text: str) -> bool | None:
    lowered = f" {text.lower()} "
    if any(w in lowered for w in _DECLINE_WORDS):
        return False
    if any(w in lowered for w in _APPROVE_WORDS):
        return True
    return None


def _resolve_reservation(pms, *, email: str, phone: str) -> str:
    """Best-effort: find the guest's current reservation, so a confirmed
    booking can be logged onto it with ``pms.add_note()``. Never blocks
    intake - an empty string just means no PMS note gets queued later."""
    if not email and not phone:
        return ""
    try:
        today = datetime.now(timezone.utc).date().isoformat()
        far = (datetime.now(timezone.utc) + timedelta(days=365)).date().isoformat()
        for res in pms.list_reservations(today, far):
            if email and res.guest.email.lower() == email.lower():
                return res.id
            if phone and res.guest.phone.replace(" ", "") == phone.replace(" ", ""):
                return res.id
    except Exception:  # noqa: BLE001 - a PMS lookup problem must not block intake
        pass
    return ""


def _enrich_from_pms(pms, *, email: str, phone: str) -> tuple[str, str]:
    """Best-effort guest_name / room_number lookup. Never blocks intake."""
    try:
        matches = pms.find_guest(email=email, phone=phone)
        if matches:
            guest = matches[0]
            return guest.full_name, str(getattr(guest, "extra", {}).get("room_number", ""))
    except Exception:  # noqa: BLE001 - PMS lookup is a nicety, not a dependency
        pass
    return "", ""


def _process_email(settings, store, pms, msg, vendors, fallbacks) -> str:
    request = sx.get_by_vendor_thread(store, msg.thread_id)
    if request is not None:
        _handle_vendor_reply(settings, store, request, msg.body_text)
        return "reply"
    category = str(msg.extra.get("category") or "concierge")
    guest_name = str(msg.extra.get("guest_name") or msg.from_name or "")
    room_number = str(msg.extra.get("room_number") or "")
    if not guest_name or not room_number:
        found_name, found_room = _enrich_from_pms(pms, email=msg.from_email, phone="")
        guest_name = guest_name or found_name
        room_number = room_number or found_room
    reservation_id = _resolve_reservation(pms, email=msg.from_email, phone="")
    return _new_request(settings, store, vendors, fallbacks, source="email",
                        external_id=msg.id, category=category, details=msg.body_text,
                        guest_name=guest_name, room_number=room_number,
                        guest_email=msg.from_email, guest_chat_id="", phone="",
                        country=str(msg.extra.get("country") or ""),
                        reservation_id=reservation_id)


def _process_message(settings, store, pms, msg, vendors, fallbacks) -> str:
    pending = sx.get_by_guest_chat(store, msg.chat_id)
    if pending is not None:
        approved = _guess_approval(msg.text)
        if approved is None:
            sx.append_thread(store, pending.id, role="guest", text=msg.text,
                             dry_run=settings.dry_run)
            log.info("guest replied but intent is unclear - resolve with "
                     "tools/request.py guest-approved/guest-declined", ref=pending.ref)
            return "unclear"
        _handle_guest_budget_reply(settings, store, pending, msg.text, approved)
        return "reply"
    category = str(msg.extra.get("category") or "concierge")
    guest_name = str(msg.extra.get("guest_name") or msg.from_name or "")
    room_number = str(msg.extra.get("room_number") or "")
    if not guest_name or not room_number:
        found_name, found_room = _enrich_from_pms(pms, email="", phone=msg.from_number)
        guest_name = guest_name or found_name
        room_number = room_number or found_room
    reservation_id = _resolve_reservation(pms, email="", phone=msg.from_number)
    return _new_request(settings, store, vendors, fallbacks, source="whatsapp",
                        external_id=msg.id, category=category, details=msg.text,
                        guest_name=guest_name, room_number=room_number, guest_email="",
                        guest_chat_id=msg.chat_id, phone=msg.from_number,
                        country=str(msg.extra.get("country") or ""),
                        reservation_id=reservation_id)


def one_pass(settings, store, *, limit: int, provider: str | None) -> tuple[int, dict]:
    stats = {"processed": 0, "drafted": 0, "needs_human": 0, "sent": 0, "escalated": 0,
             "replies": 0, "skipped": 0}
    vendors, fallbacks = _vendors_config(settings)
    with Run("new-requests", settings, store) as run:
        email = get_email(settings)
        messaging = get_messaging(settings)
        pms = get_pms(settings)

        emails = email.fetch_unread(limit=limit)
        seen_email = store.already_processed("email", [m.id for m in emails])
        chats = messaging.fetch_new(limit=limit)
        seen_chat = store.already_processed("whatsapp", [m.id for m in chats])

        for msg in emails:
            if msg.id in seen_email:
                stats["skipped"] += 1
                continue
            try:
                outcome = _process_email(settings, store, pms, msg, vendors, fallbacks)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            if not settings.dry_run:
                store.transition(store.upsert_item("email", msg.id, kind="concierge_intake_raw",
                                                   payload={"outcome": outcome}).id, "skipped")
            stats["processed"] += 1
            if outcome == "reply":
                stats["replies"] += 1
            elif outcome == "escalated":
                stats["escalated"] += 1
                stats["needs_human"] += 1
            else:
                stats["drafted"] += 1

        for msg in chats:
            if msg.id in seen_chat:
                stats["skipped"] += 1
                continue
            try:
                outcome = _process_message(settings, store, pms, msg, vendors, fallbacks)
            except LLMPendingInteractive as exc:
                run.stats = dict(stats)
                print(str(exc))
                return 3, stats
            if not settings.dry_run:
                store.transition(store.upsert_item("whatsapp", msg.id, kind="concierge_intake_raw",
                                                   payload={"outcome": outcome}).id, "skipped")
            stats["processed"] += 1
            if outcome in ("reply", "unclear"):
                stats["replies"] += 1
            elif outcome == "escalated":
                stats["escalated"] += 1
                stats["needs_human"] += 1
            else:
                stats["drafted"] += 1

        if not settings.dry_run:
            reaped = store.reap_stuck_sending()
            if reaped:
                log.warn("reaped stuck sends", count=len(reaped))
        run.stats = dict(stats)
    return 0, stats


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--once", action="store_true", help="run a single pass (default)")
    mode_group.add_argument("--watch", action="store_true",
                            help="keep running on the configured interval")
    parser.add_argument("--dry-run", action="store_true",
                        help="compute everything, write nothing, even in live mode")
    parser.add_argument("--limit", type=int, default=20, help="max messages per pass")
    parser.add_argument("--provider", default=None, help="override llm.provider for this run")
    parser.add_argument("--poll-seconds", type=int, default=None,
                        help="override the --watch interval (default: agent.yaml or 900)")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(provider=args.provider, dry_run=args.dry_run)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    sx.ensure_schema(store)
    try:
        if args.watch:
            poll_seconds = args.poll_seconds or int(
                settings.agent_get("schedule.new_requests_seconds", 900))
            while True:
                code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider)
                print(summary_line(stats, settings.mode))
                if code != 0:
                    return code
                time.sleep(poll_seconds)
        code, stats = one_pass(settings, store, limit=args.limit, provider=args.provider)
        print(summary_line(stats, settings.mode))
        return code
    except AdapterError as exc:
        print(f"integration error: {exc}", file=sys.stderr)
        print("Run `make doctor` to see what is missing and how to fix it.", file=sys.stderr)
        return 1
    except LLMError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (StoreError, WriteBlocked) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
