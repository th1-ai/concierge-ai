#!/usr/bin/env python3
"""tools/doctor.py - is Concierge AI configured and reachable right now?

    make doctor
    python3 tools/doctor.py

Runs the generic core.doctor checks (python, deps, config, .env, hotel
identity, mode, llm provider, every adapter, the store, knowledge) plus this
agent's own: the vetted vendor book, the rules block in config/agent.yaml,
and the translate/note prompt files. Exits 0 when everything passed, 1 when a
FAIL line needs fixing. Never a traceback.
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from core.config import ConfigError, Settings, load_settings, load_yaml  # noqa: E402
from core.doctor import Check, FAIL, PASS, WARN, print_table, run_checks  # noqa: E402


def check_vendors(settings: Settings) -> Check:
    raw = load_yaml("vendors")
    vendors = raw.get("vendors") or []
    fallbacks = raw.get("unvetted_fallbacks") or []
    if not vendors:
        return Check("vendor book", FAIL, "no vendors in config/vendors.yaml",
                     "Copy config/vendors.example.yaml to config/vendors.yaml - it ships "
                     "with 4 sample vendors. This is the 'trusted vendors only' guardrail.")
    missing_contact = [v.get("name", "?") for v in vendors if not v.get("contact")]
    detail = f"{len(vendors)} vetted, {len(fallbacks)} unvetted-fallback entr{'y' if len(fallbacks)==1 else 'ies'}"
    if missing_contact:
        return Check("vendor book", WARN,
                     f"{detail}; no contact email for: {', '.join(missing_contact)}",
                     "Add a `contact:` email to each vendor in config/vendors.yaml.")
    return Check("vendor book", PASS, detail)


def check_rules(settings: Settings) -> Check:
    cap = settings.agent_get("rules.budget_cap_eur", None)
    trusted_only = settings.agent_get("rules.vendor_trusted_only", None)
    if cap is None or trusted_only is None:
        return Check("concierge rules", FAIL, "rules.* missing from config/agent.yaml",
                     "Copy config/agent.example.yaml to config/agent.yaml.")
    return Check("concierge rules", PASS,
                 f"trusted_only={trusted_only}, budget_cap={cap}, "
                 f"confirm_both_sides={settings.agent_get('rules.confirm_both_sides', True)}")


def check_prompts() -> Check:
    missing = [p for p in ("prompts/translate.md", "prompts/note.md",
                           "prompts/schemas/translate.json", "prompts/schemas/note.json")
              if not (REPO_ROOT / p).is_file()]
    if missing:
        return Check("prompts", FAIL, f"missing {', '.join(missing)}",
                     "These ship with the repo - restore them from git.")
    return Check("prompts", PASS, "translate.md + note.md + schemas present")


def main() -> int:
    try:
        settings = load_settings()
    except ConfigError as exc:
        checks = run_checks(None) + [Check("config", FAIL, str(exc),
                                           "Fix config/hotel.yaml or config/agent.yaml.")]
        return print_table(checks, title="Concierge AI - doctor")

    checks = run_checks(settings, extra=[check_rules, check_vendors])
    checks.append(check_prompts())
    return print_table(checks, title="Concierge AI - doctor")


if __name__ == "__main__":
    raise SystemExit(main())
