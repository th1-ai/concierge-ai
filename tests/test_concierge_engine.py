"""Tests for tools/engine.py - the deterministic parse/match/estimate rules.
Pure functions, no I/O, no network, no fixtures needed.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.engine import (Estimate, escalation_note, estimate_for, match_vendor,
                          over_budget, parse_request, unvetted_fallback)

VENDORS = [
    {"key": "chef", "name": "Chef Ines Duarte (vetted)", "categories": ["dining", "chef"],
     "keywords": ["chef", "private chef", "in-room dining"], "unit_label": "per_cover",
     "unit_price_eur": 95},
    {"key": "transport", "name": "Aurora Executive Cars", "categories": ["transport"],
     "keywords": ["transfer", "airport", "car"], "unit_label": "per_vehicle",
     "unit_price_eur": 85, "child_seat_fee_eur": 15},
    {"key": "music", "name": "Example Conservatory duo", "categories": ["occasion", "music"],
     "keywords": ["cellist", "string duo"], "unit_label": "flat", "unit_price_eur": 220},
]
FALLBACKS = [
    {"keywords": ["sailing", "boat", "charter"], "name": "Example Bay Charters (unvetted)",
     "unit_label": "per_person", "unit_price_eur": 145},
    {"keywords": [], "name": "Unlisted supplier (unvetted)", "unit_label": "per_person",
     "unit_price_eur": 120},
]


def test_parse_request_extracts_party_child_seats_and_dietary():
    parsed = parse_request("A private chef for 4 people, one is pescatarian, "
                           "with 2 child seats needed too.")
    assert parsed.party == 4
    assert parsed.child_seats == 2
    assert parsed.dietary == "pescatarian"


def test_parse_request_does_not_read_a_duration_as_a_party_size():
    parsed = parse_request("Live music for 30 minutes please, party of 2.")
    assert parsed.party == 2  # "for 30 minutes" must not be read as 30 people
    assert parsed.duration_text == "30 minutes"


def test_parse_request_defaults_party_to_two_when_unstated():
    parsed = parse_request("Could you arrange a private chef dinner tonight?")
    assert parsed.party == 2
    assert "assuming 2" in parsed.party_source


def test_parse_request_timing_priority_weekday_beats_part_of_day():
    parsed = parse_request("We would like dinner on Saturday evening please.")
    assert parsed.when_text == "Saturday evening"


def test_parse_request_falls_back_to_clock_time_then_part_of_day():
    assert parse_request("Pickup at 14:35 please.").when_text == "14:35"
    assert parse_request("Tickets for tomorrow morning.").when_text == "morning"
    assert parse_request("No timing mentioned at all.").when_text == "the requested date"


def test_parse_request_extracts_a_flight_number():
    parsed = parse_request("Transfer needed, flight ZZ 1758, 2 child seats.")
    assert parsed.flight_number == "ZZ 1758"


def test_match_vendor_keyword_beats_category():
    # category is the vague "occasion", but "cellist" is a specific keyword -
    # the keyword hit must win (spec: "category 'occasion' is vague, the word
    # 'cellist' is not").
    vendor = match_vendor("occasion", "We would like a cellist for the evening", VENDORS)
    assert vendor is not None and vendor["key"] == "music"


def test_match_vendor_charter_never_matches_car():
    # whole-word matching: "charter" must not be read as containing "car".
    # category is deliberately not one any vendor lists, so only the keyword
    # layer is under test here.
    vendor = match_vendor("leisure", "a charter flight enquiry", VENDORS)
    assert vendor is None


def test_match_vendor_falls_back_to_category_when_no_keyword_hits():
    vendor = match_vendor("dining", "something for this evening", VENDORS)
    assert vendor is not None and vendor["key"] == "chef"


def test_match_vendor_returns_none_when_nothing_matches():
    assert match_vendor("leisure", "a sunset sailing charter", VENDORS) is None


def test_unvetted_fallback_matches_sailing_keyword():
    fallback = unvetted_fallback("leisure", "a sunset sailing charter", FALLBACKS)
    assert fallback["name"] == "Example Bay Charters (unvetted)"


def test_unvetted_fallback_uses_the_catch_all_when_nothing_matches():
    fallback = unvetted_fallback("leisure", "a hot air balloon ride", FALLBACKS)
    assert fallback["name"] == "Unlisted supplier (unvetted)"


def test_estimate_for_per_cover():
    est = estimate_for(VENDORS[0], party=4, child_seats=0, duration_text="")
    assert est.total == 380
    assert est.line == "4 x €95 per cover = €380"


def test_estimate_for_per_vehicle_with_child_seats():
    est = estimate_for(VENDORS[1], party=3, child_seats=2, duration_text="")
    assert est.total == 115
    assert "2 x €15 child seat" in est.line


def test_estimate_for_flat_ignores_party_size():
    est = estimate_for(VENDORS[2], party=10, child_seats=0, duration_text="30 minutes")
    assert est.total == 220
    assert est.line == "€220 flat for 30 minutes"


def test_over_budget_is_true_only_when_the_rule_is_on_and_over_the_cap():
    assert over_budget(600, 500, True) is True
    assert over_budget(600, 500, False) is False
    assert over_budget(400, 500, True) is False


def test_escalation_note_names_the_rule():
    note = escalation_note(category="leisure", details="a sunset sailing charter")
    assert "vendor_trusted_only" in note
