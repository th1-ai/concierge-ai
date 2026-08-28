"""tools/engine.py - Concierge AI's decision logic: parse, match, estimate,
draft. Deterministic decisioning, LLM for language (ARCHITECTURE.md section 1).

Every function down to ``build_guest_dayof_update`` is a pure function over
plain values - no I/O, no database, no adapter. That is deliberate, and it is
the whole point of the "Trusted vendors only" guardrail: ``match_vendor`` can
only ever return a supplier that is in the list you pass it (from
``config/vendors.yaml``), never one it invents. Only the last two functions,
``translate_draft`` and ``narrate``, call a model (``core.llm.complete``), and
both degrade gracefully - a translation failure returns the English draft
unchanged, a narration failure returns an empty string. Neither ever blocks
a send.

Shared by ``tools/run.py``, ``tools/chase.py`` and ``tools/request.py``, so
every place a draft gets built goes through exactly the same code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from core.llm import LLMError, LLMPendingInteractive, complete
from core.templates import build_prompt

CURRENCY_SYMBOLS = {"EUR": "€", "USD": "$", "GBP": "£"}

_WORD_NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
                 "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}
_NUM = r"(\d+|" + "|".join(_WORD_NUMBERS) + r")"

# "for 30 minutes" is a duration, not 30 people - excluded on purpose.
_PARTY_RE = re.compile(
    rf"\b(?:for|party of|table of)\s+{_NUM}\b"
    r"(?!\s*(?:minutes?|mins?|hours?|hrs?|days?|nights?|weeks?|years?|am|pm|:))",
    re.IGNORECASE)
_CHILD_SEATS_RE = re.compile(rf"\b{_NUM}\s+child\s+seats?\b", re.IGNORECASE)
_WEEKDAY_RE = re.compile(
    r"\b(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\b"
    r"(\s+(morning|afternoon|evening|sunset|midday|overnight))?", re.IGNORECASE)
_CLOCK_RE = re.compile(r"\b([01]?\d|2[0-3])[:h][0-5]\d\b")
_PART_OF_DAY_RE = re.compile(r"\b(morning|afternoon|evening|sunset|midday|overnight)\b",
                             re.IGNORECASE)
_OCCASION_RE = re.compile(
    r"\b(anniversary dinner|birthday dinner|welcome dinner|farewell dinner|"
    r"wedding breakfast|rehearsal dinner)\b", re.IGNORECASE)
_DIETARY_RE = re.compile(
    r"\b(pescatarian|vegetarian|vegan|gluten-free|dairy-free|"
    r"nut allerg\w*|shellfish allerg\w*)\b", re.IGNORECASE)
_DURATION_RE = re.compile(r"\b(\d+)\s*(minutes?|mins?|hours?|hrs?)\b", re.IGNORECASE)
_FLIGHT_RE = re.compile(r"\bflight\s+([A-Za-z]{1,3}\s?\d{2,4})\b", re.IGNORECASE)


def _to_int(token: str) -> int:
    if token.isdigit():
        return int(token)
    return _WORD_NUMBERS.get(token.lower(), 0)


@dataclass
class ParsedRequest:
    """What ``parse_request`` pulls out of a guest's own words. See spec section 3.1."""

    party: int = 2
    party_source: str = "no party size stated, assuming 2"
    child_seats: int = 0
    when_text: str = "the requested date"
    dietary: str = ""
    duration_text: str = ""
    flight_number: str = ""


def parse_request(details: str) -> ParsedRequest:
    """Regex extraction only - conservative on purpose (see module docstring)."""
    text = details or ""
    result = ParsedRequest()

    m = _PARTY_RE.search(text)
    if m:
        result.party = _to_int(m.group(1))
        result.party_source = f"parsed from \"{m.group(0).strip()}\""

    m = _CHILD_SEATS_RE.search(text)
    if m:
        result.child_seats = _to_int(m.group(1))

    m = _WEEKDAY_RE.search(text)
    if m:
        result.when_text = m.group(0).strip()
    else:
        m = _CLOCK_RE.search(text)
        if m:
            result.when_text = m.group(0)
        else:
            m = _PART_OF_DAY_RE.search(text)
            if m:
                result.when_text = m.group(0).lower()
            else:
                m = _OCCASION_RE.search(text)
                if m:
                    result.when_text = m.group(0).lower()

    m = _DIETARY_RE.search(text)
    if m:
        result.dietary = m.group(0).lower()

    m = _DURATION_RE.search(text)
    if m:
        result.duration_text = m.group(0).lower()

    m = _FLIGHT_RE.search(text)
    if m:
        result.flight_number = m.group(1).upper().strip()

    return result


def _keyword_hit(keyword: str, haystack: str) -> bool:
    """Whole-word match, tolerant of hyphen/space variants ('in-room dining'
    matches 'in room dining'; 'charter' never matches inside 'car')."""
    parts = [p for p in re.split(r"[-\s]+", keyword.strip()) if p]
    if not parts:
        return False
    body = r"[-\s]+".join(re.escape(p) for p in parts)
    return re.search(rf"\b{body}\b", haystack, re.IGNORECASE) is not None


def match_vendor(category: str, details: str, vendors: list[dict]) -> dict | None:
    """A keyword hit wins over a category match - see config/vendors.yaml's header."""
    haystack = f"{category} {details}".lower()
    for vendor in vendors:
        if any(_keyword_hit(k, haystack) for k in vendor.get("keywords", [])):
            return vendor
    needle = (category or "").strip().lower()
    if needle:
        for vendor in vendors:
            if needle in [c.lower() for c in vendor.get("categories", [])]:
                return vendor
    return None


def unvetted_fallback(category: str, details: str, fallbacks: list[dict]) -> dict:
    """The supplier used ONLY when no vetted vendor matches and the trusted-only
    rule is off. Every name it returns carries "(unvetted)" - see docs/safety.md."""
    haystack = f"{category} {details}".lower()
    catch_all = None
    for entry in fallbacks:
        keywords = entry.get("keywords") or []
        if not keywords:
            catch_all = entry
            continue
        if any(_keyword_hit(k, haystack) for k in keywords):
            return dict(entry, key="unvetted", channel="email", contact="")
    fallback = dict(catch_all or {"name": "Unlisted supplier (unvetted)",
                                  "unit_label": "per_person", "unit_price_eur": 120})
    fallback["name"] = fallback.get("name", "Unlisted supplier (unvetted)").replace(
        "{{category}}", category or "unlisted")
    fallback.setdefault("key", "unvetted")
    fallback.setdefault("channel", "email")
    fallback.setdefault("contact", "")
    return fallback


def fmt_money(amount: float, currency: str = "EUR") -> str:
    symbol = CURRENCY_SYMBOLS.get(currency, currency + " ")
    return f"{symbol}{amount:,.0f}"


@dataclass
class Estimate:
    total: float
    line: str


def estimate_for(vendor: dict, party: int, child_seats: int, duration_text: str,
                 currency: str = "EUR") -> Estimate:
    """Spec section 3.4, verbatim formulas."""
    unit_label = vendor.get("unit_label", "per_person")
    unit_price = float(vendor.get("unit_price_eur", 0))
    if unit_label == "per_vehicle":
        fee = float(vendor.get("child_seat_fee_eur", 0))
        total = unit_price + child_seats * fee
        if child_seats > 0:
            line = (f"{fmt_money(unit_price, currency)} per vehicle + {child_seats} x "
                    f"{fmt_money(fee, currency)} child seat = {fmt_money(total, currency)}")
        else:
            line = f"{fmt_money(unit_price, currency)} per vehicle"
        return Estimate(total, line)
    if unit_label == "flat":
        total = unit_price
        line = f"{fmt_money(unit_price, currency)} flat"
        if duration_text:
            line += f" for {duration_text}"
        return Estimate(total, line)
    label_words = {"per_cover": "per cover", "per_person": "per person"}.get(
        unit_label, unit_label.replace("_", " "))
    total = unit_price * max(party, 1)
    line = f"{party} x {fmt_money(unit_price, currency)} {label_words} = {fmt_money(total, currency)}"
    return Estimate(total, line)


def over_budget(total: float, cap: float, rule_on: bool) -> bool:
    """Spec section 3.5: only ever true when the guardrail is actually on."""
    return bool(rule_on) and total > cap


def _signature(hotel_name: str) -> str:
    return f"Thank you,\nConcierge desk, {hotel_name}"


def build_guest_ack(*, hotel_name: str, ref: str, category: str, details: str,
                    vendor_name: str | None) -> dict:
    """The first guest-facing message: "we're on it". Sent alongside the vendor
    outreach (or alone, when the request is escalated)."""
    subject = f"We're on it: your {category} request ({ref})"
    if vendor_name:
        opening = (f"Thank you for your request. We are reaching out to {vendor_name} "
                   "on your behalf now and will confirm as soon as we hear back.")
    else:
        opening = ("Thank you for your request. This one needs a closer look from our "
                   "team before we can confirm a supplier, and someone will be in touch "
                   "shortly.")
    body = f"{opening}\n\nYour request: \"{details}\"\n\n{_signature(hotel_name)}"
    return {"subject": subject, "body": body}


def build_vendor_outreach(*, hotel_name: str, ref: str, vendor: dict, party: int,
                          when_text: str, dietary: str, child_seats: int,
                          duration_text: str, details: str, estimate: Estimate,
                          flight_number: str = "") -> dict:
    """One template per vendor key, each ending in a specific confirming question
    (spec section 7). Every draft embeds the guest's own words verbatim."""
    key = vendor.get("key", "")
    subject = f"Request for {when_text} ({ref})"
    if key == "chef":
        ask = ("Can you confirm availability and a start time, and tell me what time "
              "you would arrive to set up?")
        dietary_line = f" One of the party is {dietary}." if dietary else ""
        body = (f"A guest at {hotel_name} would like a private chef dinner on "
               f"{when_text}, {party} covers.{dietary_line}\n\n"
               f"Guest's own words: \"{details}\"\n\n"
               f"At your usual rate that is {estimate.line}. {ask}")
    elif key == "transport":
        seat_line = (f"We need {child_seats} child seat(s) fitted before the car "
                    "leaves. Please do not send it without them.\n\n"
                    if child_seats > 0 else "")
        flight_line = f" The flight is {flight_number}." if flight_number else ""
        body = (f"A guest at {hotel_name} needs a transfer on {when_text} for a party "
               f"of {party}.{flight_line}\n\n{seat_line}"
               f"Guest's own words: \"{details}\"\n\n"
               f"That is {estimate.line}. Please confirm the driver's name, plate and "
               f"mobile number so we can text them ahead.")
    elif key == "tickets":
        body = (f"A guest at {hotel_name} would like tickets for {when_text}, party of "
               f"{party}.\n\nGuest's own words: \"{details}\"\n\n"
               f"That is {estimate.line}. Please confirm availability, how the tickets "
               f"are delivered (QR code or physical), and the entry window.")
    elif key == "music":
        body = (f"A guest at {hotel_name} would like live music on {when_text}"
               f"{' for ' + duration_text if duration_text else ''}.\n\n"
               f"Guest's own words: \"{details}\"\n\n"
               f"That is {estimate.line}. Please confirm the player, and that they are "
               "happy to stay at the quiet end of the repertoire.")
    else:
        body = (f"A guest at {hotel_name} has asked for the following, for "
               f"{when_text}, party of {party}:\n\n\"{details}\"\n\n"
               f"Our estimate is {estimate.line}. Please confirm you can take this on, "
               "and the details we need to pass to the guest.")
    return {"subject": subject, "body": f"{body}\n\n{_signature(hotel_name)}"}


def build_budget_confirm(*, hotel_name: str, ref: str, details: str,
                         estimate: Estimate, cap: float, currency: str = "EUR") -> dict:
    """Asks the GUEST to sign off before the vendor is contacted at all - this is
    how the roster's "won't promise a price it can't yet confirm" is kept true
    even over the cap: nothing goes to a vendor until this is approved."""
    subject = f"Confirming the cost before we book ({ref})"
    body = (f"Your request (\"{details}\") comes to {estimate.line}, which is over "
           f"our usual {fmt_money(cap, currency)} check-in point for a request like "
           "this, so we wanted to confirm with you before booking it.\n\n"
           "Reply to this message to confirm and we will book it right away.\n\n"
           f"{_signature(hotel_name)}")
    return {"subject": subject, "body": body}


def build_vendor_chase(*, hotel_name: str, ref: str, vendor_name: str,
                       follow_up_count: int) -> dict:
    subject = f"Following up ({ref})"
    body = (f"Checking in on the request below for {ref}. We have not heard back "
           f"yet and would appreciate a confirmation when you have a moment. "
           f"(check-in #{follow_up_count})\n\n{_signature(hotel_name)}")
    return {"subject": subject, "body": body}


def build_vendor_ack(*, hotel_name: str, ref: str, vendor_reply_text: str) -> dict:
    """Sent to the vendor FIRST when closing the loop - "the vendor hears the
    booking landed before the guest is told" (spec section 3, useCloseLoop)."""
    subject = f"Confirmed, thank you ({ref})"
    body = (f"Thank you, that is perfect. We have this locked in on our side:\n\n"
           f"\"{vendor_reply_text.strip()}\"\n\n"
           f"We will let the guest know now. Appreciate you fitting this in.\n\n"
           f"{_signature(hotel_name)}")
    return {"subject": subject, "body": body}


def build_guest_confirmation(*, hotel_name: str, ref: str, vendor_name: str,
                             estimate: Estimate, vendor_reply_text: str,
                             currency: str = "EUR") -> dict:
    """Sent to the guest SECOND when closing the loop, after the vendor ack."""
    subject = f"Confirmed ({ref})"
    body = (f"Good news, your request is confirmed with {vendor_name}:\n\n"
           f"\"{vendor_reply_text.strip()}\"\n\n"
           f"{fmt_money(estimate.total, currency)} will be added to your account for "
           "our team to confirm at checkout.\n\n"
           f"{_signature(hotel_name)}")
    return {"subject": subject, "body": body}


def build_dayof_check(*, hotel_name: str, ref: str, vendor_name: str,
                      flight_number: str = "") -> dict:
    """Transport only - the spec's "final check with the provider... flight number
    in hand" beat. Nothing is assumed, everything is confirmed."""
    subject = f"Today's pickup ({ref})"
    flight_line = f" The flight to track is {flight_number}." if flight_number else ""
    body = (f"Quick check for today's pickup, {ref}. Can you confirm the driver is "
           f"on schedule?{flight_line}\n\n{_signature(hotel_name)}")
    return {"subject": subject, "body": body}


def build_guest_dayof_update(*, hotel_name: str, ref: str,
                             vendor_dayof_reply_text: str) -> dict:
    subject = f"You're all set for today ({ref})"
    body = (f"Just confirming everything is on track for today: "
           f"\"{vendor_dayof_reply_text.strip()}\"\n\n"
           "There is nothing further you need to do.\n\n"
           f"{_signature(hotel_name)}")
    return {"subject": subject, "body": body}


def escalation_note(*, category: str, details: str, rule_off_hint: bool = True) -> str:
    """Internal-only text for a request with no vetted vendor. Never sent to
    anyone - it is what a human sees in the review queue instead of a draft."""
    note = (f"No vetted vendor for \"{category}\" (request: \"{details}\"). "
           "Escalated per rules.vendor_trusted_only.")
    if rule_off_hint:
        note += (" Turn that rule off in config/agent.yaml and re-run to see the "
                 "unvetted-fallback path instead.")
    return note


LANGUAGE_NAMES = {"en": "English", "fr": "French", "de": "German", "es": "Spanish",
                  "it": "Italian", "pt": "Portuguese", "nl": "Dutch", "sv": "Swedish"}


def translate_draft(settings: Any, draft: dict, target_lang: str, *,
                    store: Any = None, item_id: str | None = None,
                    fixture_id: str | None = None, provider: str | None = None) -> dict:
    """Localize a guest-facing draft. Degrades to the English draft, unchanged,
    on a genuine failure - a translation problem must never block a send or
    corrupt a fact (ARCHITECTURE.md section 1: deterministic decisioning, LLM
    for language). ``LLMPendingInteractive`` is NOT a failure - it means the
    ``interactive`` provider parked a prompt for the hotel's Claude session,
    and it is re-raised so the caller's own pending-prompt handling (exit code
    3) runs, same as every other reasoning step in this family."""
    if target_lang == "en" or not target_lang:
        return draft
    try:
        prompt = build_prompt(
            "translate", settings=settings, item=draft,
            target_language=LANGUAGE_NAMES.get(target_lang, target_lang),
            fixture_id=fixture_id)
        result = complete("translate", prompt, schema=_translate_schema(), settings=settings,
                          provider=provider, store=store, item_id=item_id,
                          fixture_id=fixture_id)
        data = result.data or {}
        if not data.get("body"):
            return draft
        return {"subject": data.get("subject") or draft.get("subject", ""),
               "body": data["body"]}
    except LLMPendingInteractive:
        raise
    except LLMError:
        return draft


def narrate(settings: Any, summary: dict, *, store: Any = None, item_id: str | None = None,
           fixture_id: str | None = None, provider: str | None = None) -> str:
    """A cosmetic one-line internal note. Never raises on a genuine failure,
    never blocks a send - mirrors the spec's own /api/concierge-note: the page
    (here, the log) is fine on null. ``LLMPendingInteractive`` still
    propagates (see ``translate_draft``) so ``tools/report.py --narrate``
    can park a prompt for the hotel's Claude session instead of silently
    printing nothing."""
    try:
        prompt = build_prompt("note", settings=settings, item=summary, fixture_id=fixture_id)
        result = complete("note", prompt, schema=_note_schema(), settings=settings,
                          provider=provider, store=store, item_id=item_id,
                          fixture_id=fixture_id)
        return (result.data or {}).get("note", "")
    except LLMPendingInteractive:
        raise
    except LLMError:
        return ""


def _translate_schema() -> dict:
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "prompts" / "schemas" / "translate.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _note_schema() -> dict:
    import json
    from pathlib import Path
    path = Path(__file__).resolve().parent.parent / "prompts" / "schemas" / "note.json"
    return json.loads(path.read_text(encoding="utf-8"))
