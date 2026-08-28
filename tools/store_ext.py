"""tools/store_ext.py - Concierge AI's own table, layered on core.store.Store.

The generic `items` table (core/store.py) is the review queue: one row per
draft message waiting on a human approval. It is not a booking ledger. This
module adds the one table Concierge AI needs to track a request end to end -
`concierge_requests` - plus small, pure-ish helpers the engine, `tools/run.py`
and `tools/request.py` all share.

Call :func:`ensure_schema` once per `Store`, right after constructing it;
every tool in this repo does. Nothing here replaces `core.store` - it uses
the same connection (`store.db`), the same `utcnow()` convention, and the
same JSON-column convention `core.store` itself uses.

Idempotency note: `ref` is issued once via `core.store.Store.next_sequence`,
which is never bumped on `--dry-run` - a rehearsal can never burn a REQ number.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from typing import Any

from core.store import Store, utcnow

SCHEMA = """
CREATE TABLE IF NOT EXISTS concierge_requests (
  id                TEXT PRIMARY KEY,
  ref               TEXT NOT NULL UNIQUE,
  guest_name        TEXT,
  guest_email       TEXT,
  guest_chat_id     TEXT,
  room_number       TEXT,
  reservation_id    TEXT,
  category          TEXT NOT NULL,
  details           TEXT NOT NULL,
  source            TEXT NOT NULL,
  external_id       TEXT,
  language          TEXT NOT NULL DEFAULT 'en',
  language_note     TEXT NOT NULL DEFAULT '',
  party             INTEGER NOT NULL DEFAULT 2,
  party_source      TEXT,
  child_seats       INTEGER NOT NULL DEFAULT 0,
  when_text         TEXT,
  dietary           TEXT,
  duration_text     TEXT,
  flight_number     TEXT,
  vendor_key        TEXT,
  vendor_name       TEXT,
  vendor_contact    TEXT,
  vendor_unvetted   INTEGER NOT NULL DEFAULT 0,
  unit_label        TEXT,
  unit_price_eur    REAL,
  estimate_total    REAL,
  estimate_line     TEXT,
  over_budget       INTEGER NOT NULL DEFAULT 0,
  vendor_thread_id  TEXT,
  pipeline_status   TEXT NOT NULL DEFAULT 'new',
  thread_json       TEXT,
  created_at        TEXT NOT NULL,
  updated_at        TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_concierge_pipeline
  ON concierge_requests (pipeline_status, updated_at DESC);
CREATE UNIQUE INDEX IF NOT EXISTS idx_concierge_source_external
  ON concierge_requests (source, external_id) WHERE external_id IS NOT NULL;
"""

PIPELINE_STATUSES = (
    "new", "in_progress", "awaiting_guest_budget", "awaiting_vendor",
    "confirmed", "done", "escalated",
)


@dataclass
class ConciergeRequest:
    """One row of ``concierge_requests`` - the whole job, start to finish."""

    id: str
    ref: str
    category: str
    details: str
    source: str
    guest_name: str = ""
    guest_email: str = ""
    guest_chat_id: str = ""
    room_number: str = ""
    reservation_id: str = ""
    language: str = "en"
    #: non-empty only when the guest's detected language is not in
    #: ``hotel.languages`` - see ``tools/run.py``'s language guardrail. The
    #: draft still goes out in ``language`` (the hotel's default in that
    #: case), but the item is queued ``needs_human`` with this as the reason.
    language_note: str = ""
    party: int = 2
    party_source: str = ""
    child_seats: int = 0
    when_text: str = ""
    dietary: str = ""
    duration_text: str = ""
    flight_number: str = ""
    vendor_key: str = ""
    vendor_name: str = ""
    vendor_contact: str = ""
    vendor_unvetted: bool = False
    unit_label: str = ""
    unit_price_eur: float = 0.0
    estimate_total: float = 0.0
    estimate_line: str = ""
    over_budget: bool = False
    vendor_thread_id: str = ""
    pipeline_status: str = "new"
    thread: list = field(default_factory=list)
    external_id: str = ""
    created_at: str = ""
    updated_at: str = ""

    @classmethod
    def from_row(cls, row: Any) -> "ConciergeRequest":
        return cls(
            id=row["id"], ref=row["ref"], category=row["category"], details=row["details"],
            source=row["source"], external_id=row["external_id"] or "",
            guest_name=row["guest_name"] or "",
            guest_email=row["guest_email"] or "", guest_chat_id=row["guest_chat_id"] or "",
            room_number=row["room_number"] or "", reservation_id=row["reservation_id"] or "",
            language=row["language"] or "en", language_note=row["language_note"] or "",
            party=row["party"] or 2,
            party_source=row["party_source"] or "", child_seats=row["child_seats"] or 0,
            when_text=row["when_text"] or "", dietary=row["dietary"] or "",
            duration_text=row["duration_text"] or "", flight_number=row["flight_number"] or "",
            vendor_key=row["vendor_key"] or "", vendor_name=row["vendor_name"] or "",
            vendor_contact=row["vendor_contact"] or "", vendor_unvetted=bool(row["vendor_unvetted"]),
            unit_label=row["unit_label"] or "", unit_price_eur=row["unit_price_eur"] or 0.0,
            estimate_total=row["estimate_total"] or 0.0, estimate_line=row["estimate_line"] or "",
            over_budget=bool(row["over_budget"]), vendor_thread_id=row["vendor_thread_id"] or "",
            pipeline_status=row["pipeline_status"], thread=json.loads(row["thread_json"] or "[]"),
            created_at=row["created_at"], updated_at=row["updated_at"])


def ensure_schema(store: Store) -> None:
    store.migrate(SCHEMA)


def next_ref(store: Store, *, dry_run: bool = False) -> str:
    """``REQ-1001``, ``REQ-1002``... - a stable id every thread and note hangs off."""
    return f"REQ-{1000 + store.next_sequence('concierge_ref', dry_run=dry_run)}"


def create_request(store: Store, *, dry_run: bool = False, **fields: Any) -> ConciergeRequest:
    """``dry_run=True`` (``tools/run.py --once --dry-run``) computes and
    returns the request exactly as it would be, but never inserts the row -
    not even to check idempotency later. Two dry-run passes over the same
    fixtures must never collide on the same peeked ``ref``
    (``core.store.Store.next_sequence(dry_run=True)``); the fix is that
    nothing here writes a row for either of them to collide on."""
    now = utcnow()
    row_id = uuid.uuid4().hex
    ref = fields.pop("ref")
    thread = fields.pop("thread", [])
    if dry_run:
        fields.setdefault("vendor_unvetted", False)
        fields.setdefault("over_budget", False)
        fields["vendor_unvetted"] = bool(fields["vendor_unvetted"])
        fields["over_budget"] = bool(fields["over_budget"])
        return ConciergeRequest(id=row_id, ref=ref, thread=thread,
                                created_at=now, updated_at=now, **fields)
    cols = ["id", "ref", "thread_json", "created_at", "updated_at"] + list(fields.keys())
    values = [row_id, ref, json.dumps(thread, ensure_ascii=False), now, now] + list(fields.values())
    placeholders = ",".join("?" * len(cols))
    store.db.execute(
        f"INSERT INTO concierge_requests ({','.join(cols)}) VALUES ({placeholders})",
        values)
    req = get_request(store, row_id)
    assert req is not None
    return req


def get_request(store: Store, request_id: str) -> ConciergeRequest | None:
    row = store.db.execute("SELECT * FROM concierge_requests WHERE id=?", (request_id,)).fetchone()
    return ConciergeRequest.from_row(row) if row else None


def get_by_ref(store: Store, ref: str) -> ConciergeRequest | None:
    row = store.db.execute("SELECT * FROM concierge_requests WHERE ref=?", (ref,)).fetchone()
    return ConciergeRequest.from_row(row) if row else None


def get_by_source_external(store: Store, source: str, external_id: str) -> ConciergeRequest | None:
    """Idempotency guard for ``tools/run.py:_new_request()``. If a model
    reasoning step (translation) pauses partway through drafting a fresh
    request - ``llm.provider: interactive`` parks a prompt - a retry must
    resume the SAME request (same ``ref``, same thread ids), never issue a
    second one. Call this before ``next_ref()``/``create_request()``."""
    if not external_id:
        return None
    row = store.db.execute(
        "SELECT * FROM concierge_requests WHERE source=? AND external_id=?",
        (source, external_id)).fetchone()
    return ConciergeRequest.from_row(row) if row else None


def get_by_vendor_thread(store: Store, thread_id: str) -> ConciergeRequest | None:
    """Find the request a vendor's reply belongs to, by the thread id it was
    sent on. Only ever matches a request still ``awaiting_vendor`` - a reply
    on an old, already-closed thread is left alone."""
    if not thread_id:
        return None
    row = store.db.execute(
        "SELECT * FROM concierge_requests WHERE vendor_thread_id=? "
        "AND pipeline_status='awaiting_vendor'", (thread_id,)).fetchone()
    return ConciergeRequest.from_row(row) if row else None


def get_by_guest_chat(store: Store, chat_id: str) -> ConciergeRequest | None:
    """Find the request a guest's WhatsApp reply belongs to, when that request
    is waiting on a budget sign-off."""
    if not chat_id:
        return None
    row = store.db.execute(
        "SELECT * FROM concierge_requests WHERE guest_chat_id=? "
        "AND pipeline_status='awaiting_guest_budget'", (chat_id,)).fetchone()
    return ConciergeRequest.from_row(row) if row else None


def list_requests(store: Store, *, pipeline_status: str | None = None,
                  limit: int = 100) -> list[ConciergeRequest]:
    if pipeline_status:
        rows = store.db.execute(
            "SELECT * FROM concierge_requests WHERE pipeline_status=? "
            "ORDER BY updated_at ASC LIMIT ?", (pipeline_status, limit)).fetchall()
    else:
        rows = store.db.execute(
            "SELECT * FROM concierge_requests ORDER BY updated_at ASC LIMIT ?",
            (limit,)).fetchall()
    return [ConciergeRequest.from_row(r) for r in rows]


def update_request(store: Store, request_id: str, *, dry_run: bool = False,
                   **fields: Any) -> ConciergeRequest | None:
    """``dry_run=True`` writes nothing - not even a status update - and
    returns ``None``. No caller in this repo reads the return value when it
    passes ``dry_run=True`` (they only use it to advance ``pipeline_status``
    as a side effect); a caller that needs the row back must not be in a
    dry run."""
    if dry_run:
        return None
    if not fields:
        req = get_request(store, request_id)
        assert req is not None
        return req
    cols = ", ".join(f"{k}=?" for k in fields)
    store.db.execute(f"UPDATE concierge_requests SET {cols}, updated_at=? WHERE id=?",
                     [*fields.values(), utcnow(), request_id])
    req = get_request(store, request_id)
    assert req is not None
    return req


def append_thread(store: Store, request_id: str, *, role: str, text: str,
                  to: str | None = None, dry_run: bool = False) -> ConciergeRequest | None:
    """Append one line to the request's thread. ``role`` is guest|ai|vendor.
    ``dry_run=True`` writes nothing and returns ``None`` - see
    :func:`update_request`."""
    if dry_run:
        return None
    req = get_request(store, request_id)
    if req is None:
        raise KeyError(f"no concierge request {request_id}")
    entry = {"ts": utcnow(), "role": role, "text": text}
    if to:
        entry["to"] = to
    thread = req.thread + [entry]
    store.db.execute("UPDATE concierge_requests SET thread_json=?, updated_at=? WHERE id=?",
                     (json.dumps(thread, ensure_ascii=False), utcnow(), request_id))
    updated = get_request(store, request_id)
    assert updated is not None
    return updated
