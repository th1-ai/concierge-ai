#!/usr/bin/env python3
"""tools/request.py - the domain actions that do not fit `tools/review.py`.

    python3 tools/request.py add --category dining --details "..." \\
        --guest-name "..." --room 214 [--source front_desk|phone]
    python3 tools/request.py list [--status awaiting_vendor]
    python3 tools/request.py show <ref>
    python3 tools/request.py guest-approved <ref>
    python3 tools/request.py guest-declined <ref>
    python3 tools/request.py log-reply <ref> --text "..."
    python3 tools/request.py close-loop <ref> [--vendor-reply "..."]
    python3 tools/request.py dayof-sweep
    python3 tools/request.py dayof-replied <ref> --text "..."

`add` is how a phone call or a front-desk conversation - the two channels
this agent has no adapter for - becomes a request: someone types what the
guest asked for. Email and WhatsApp requests arrive automatically through
`tools/run.py`. Every command here only ever queues a draft; nothing is sent
without going through `workflows/80-review.md`.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
TOOLS_DIR = Path(__file__).resolve().parent
if str(TOOLS_DIR) not in sys.path:
    sys.path.insert(0, str(TOOLS_DIR))

import engine  # noqa: E402
import run as intake  # noqa: E402
import store_ext as sx  # noqa: E402
from core.config import ConfigError, load_settings  # noqa: E402
from core.llm import LLMPendingInteractive  # noqa: E402
from core.store import Store, StoreError  # noqa: E402


def _get_request_or_die(store, ref: str):
    request = sx.get_by_ref(store, ref)
    if request is None:
        print(f"error: no request {ref}", file=sys.stderr)
        raise SystemExit(1)
    return request


def cmd_add(settings, store, args) -> int:
    vendors, fallbacks = intake._vendors_config(settings)
    external_id = f"manual-{uuid.uuid4().hex[:8]}"
    outcome = intake._new_request(
        settings, store, vendors, fallbacks, source=args.source, external_id=external_id,
        category=args.category, details=args.details, guest_name=args.guest_name or "",
        room_number=args.room or "", guest_email=args.guest_email or "", guest_chat_id="",
        phone="", country="")
    print(f"{outcome}: run `python3 tools/review.py list` to see what was drafted.")
    return 0


def cmd_list(store, args) -> int:
    requests = sx.list_requests(store, pipeline_status=args.status, limit=args.limit)
    if not requests:
        print("No requests match.")
        return 0
    print(f"{len(requests)} request(s):\n")
    for r in requests:
        vendor = r.vendor_name or "(none)"
        print(f"  {r.ref}  {r.pipeline_status:<22} {r.category:<12} {vendor:<32} "
             f"{r.details[:40]}")
    return 0


def cmd_show(store, ref: str) -> int:
    request = _get_request_or_die(store, ref)
    print(json.dumps(request.__dict__, indent=2, ensure_ascii=False, default=str))
    return 0


def cmd_guest_decision(settings, store, ref: str, approved: bool, text: str) -> int:
    request = _get_request_or_die(store, ref)
    if request.pipeline_status != "awaiting_guest_budget":
        print(f"error: {ref} is '{request.pipeline_status}', not waiting on a guest "
             "budget decision", file=sys.stderr)
        return 1
    intake._handle_guest_budget_reply(settings, store, request, text, approved)
    print(f"{ref}: recorded guest {'approval' if approved else 'decline'}.")
    return 0


def cmd_log_reply(settings, store, ref: str, text: str) -> int:
    request = _get_request_or_die(store, ref)
    if request.pipeline_status != "awaiting_vendor":
        print(f"error: {ref} is '{request.pipeline_status}', not awaiting a vendor reply",
             file=sys.stderr)
        return 1
    intake._handle_vendor_reply(settings, store, request, text)
    print(f"{ref}: reply logged.")
    return 0


def cmd_close_loop(settings, store, ref: str, vendor_reply: str | None) -> int:
    request = _get_request_or_die(store, ref)
    if request.pipeline_status not in ("awaiting_vendor", "confirmed"):
        print(f"error: {ref} is '{request.pipeline_status}', nothing to close", file=sys.stderr)
        return 1
    if vendor_reply:
        text, log_it = vendor_reply, True
    else:
        vendor_lines = [t["text"] for t in request.thread if t.get("role") == "vendor"]
        if not vendor_lines:
            print(f"error: no vendor reply logged yet for {ref} - pass --vendor-reply",
                 file=sys.stderr)
            return 1
        text, log_it = vendor_lines[-1], False
    intake._handle_vendor_reply(settings, store, request, text, force=True, log_it=log_it)
    print(f"{ref}: closed the loop (vendor ack + guest confirmation queued for review).")
    return 0


def cmd_dayof_sweep(settings, store) -> int:
    requests = [r for r in sx.list_requests(store, pipeline_status="confirmed")
               if r.vendor_key == "transport"]
    if not requests:
        print("Nothing to check today.")
        return 0
    for r in requests:
        draft = engine.build_dayof_check(hotel_name=settings.hotel.name, ref=r.ref,
                                         vendor_name=r.vendor_name,
                                         flight_number=r.flight_number)
        item = store.upsert_item("concierge", f"{r.ref}:dayof_check", kind="concierge_dayof_check",
                                 payload={"ref": r.ref, "channel": "email", "to": r.vendor_contact})
        if item.review_status == "new":
            store.set_fields(item.id, draft=draft)
            store.transition(item.id, "pending_review", actor="agent")
        print(f"{r.ref}: drafted the day-of check to {r.vendor_name}.")
    return 0


def cmd_dayof_replied(settings, store, ref: str, text: str) -> int:
    request = _get_request_or_die(store, ref)
    sx.append_thread(store, request.id, role="vendor", text=text, to="guest")
    update = engine.build_guest_dayof_update(hotel_name=settings.hotel.name, ref=ref,
                                             vendor_dayof_reply_text=text)
    update = engine.translate_draft(settings, update, request.language, store=store,
                                    fixture_id=f"{ref}-dayof-update")
    item = store.upsert_item(
        "concierge", f"{ref}:dayof_update", kind="concierge_guest_dayof_update",
        payload={"ref": ref, "channel": "email" if request.guest_email else "messaging",
                "to": request.guest_email or request.guest_chat_id})
    if item.review_status == "new":
        store.set_fields(item.id, draft=update)
        store.transition(item.id, "pending_review", actor="agent")
    sx.update_request(store, request.id, pipeline_status="done")
    print(f"{ref}: drafted the final guest update.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="log a request that came in by phone or at the desk")
    p_add.add_argument("--category", required=True)
    p_add.add_argument("--details", required=True)
    p_add.add_argument("--guest-name", default="")
    p_add.add_argument("--room", default="")
    p_add.add_argument("--guest-email", default="")
    p_add.add_argument("--source", default="front_desk", choices=["front_desk", "phone"])

    p_list = sub.add_parser("list", help="the pipeline board")
    p_list.add_argument("--status", default=None, choices=list(sx.PIPELINE_STATUSES))
    p_list.add_argument("--limit", type=int, default=100)

    p_show = sub.add_parser("show", help="full detail for one request")
    p_show.add_argument("ref")

    p_ga = sub.add_parser("guest-approved", help="guest signed off on an over-budget estimate")
    p_ga.add_argument("ref")
    p_ga.add_argument("--text", default="(approved by phone/desk)")

    p_gd = sub.add_parser("guest-declined", help="guest declined the estimate")
    p_gd.add_argument("ref")
    p_gd.add_argument("--text", default="(declined by phone/desk)")

    p_lr = sub.add_parser("log-reply", help="record a vendor reply that arrived off-channel")
    p_lr.add_argument("ref")
    p_lr.add_argument("--text", required=True)

    p_cl = sub.add_parser("close-loop", help="close the loop now (vendor ack + guest confirmation)")
    p_cl.add_argument("ref")
    p_cl.add_argument("--vendor-reply", default=None)

    sub.add_parser("dayof-sweep", help="draft today's vendor check for confirmed transfers")

    p_dr = sub.add_parser("dayof-replied", help="vendor confirmed the day-of check")
    p_dr.add_argument("ref")
    p_dr.add_argument("--text", required=True)

    args = parser.parse_args(argv)

    try:
        settings = load_settings()
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 1

    store = Store(settings)
    sx.ensure_schema(store)
    try:
        if args.command == "add":
            return cmd_add(settings, store, args)
        if args.command == "list":
            return cmd_list(store, args)
        if args.command == "show":
            return cmd_show(store, args.ref)
        if args.command == "guest-approved":
            return cmd_guest_decision(settings, store, args.ref, True, args.text)
        if args.command == "guest-declined":
            return cmd_guest_decision(settings, store, args.ref, False, args.text)
        if args.command == "log-reply":
            return cmd_log_reply(settings, store, args.ref, args.text)
        if args.command == "close-loop":
            return cmd_close_loop(settings, store, args.ref, args.vendor_reply)
        if args.command == "dayof-sweep":
            return cmd_dayof_sweep(settings, store)
        if args.command == "dayof-replied":
            return cmd_dayof_replied(settings, store, args.ref, args.text)
        parser.error(f"unknown command {args.command}")
        return 2
    except LLMPendingInteractive as exc:
        # A translate step (guest confirmation / day-of update) parked a
        # prompt for the hotel's Claude session - same convention as
        # tools/run.py: exit 3 is not an error.
        print(str(exc))
        return 3
    except StoreError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


if __name__ == "__main__":
    raise SystemExit(main())
