# Workflow: first-run setup

Objective: get Concierge AI from a fresh clone to a working demo, then to real
config, in one sitting.

## Steps

1. **Install and check.**
   ```bash
   make setup
   make doctor
   ```
   `make setup` creates the virtualenv, installs `requirements.txt`, and
   copies `.env.example` -> `.env` and every `config/*.example.yaml` ->
   `config/*.yaml` (only if those files do not exist yet - it never overwrites
   your own copies; this includes `config/vendors.yaml`, the vetted supplier
   book). `make doctor` will show a `FAIL` on "hotel identity" right after
   setup - that is expected, it means the property name is still the shipped
   placeholder. Everything else should be `ok` or `warn`.

2. **Run the demo.** No credentials needed.
   ```bash
   make demo
   ```
   Expect to see 6 inbound items processed: 5 new requests (a private chef
   booking, an escalation with no vetted vendor for "sunset sailing", an
   airport transfer with child seats, a tickets request, and a WhatsApp
   request in French that comes back translated) plus the chef vendor's
   reply, which arrives in the same fixture batch and resolves that booking
   inline. The last line reads
   `DEMO OK — 6 items processed, 4 drafted, 0 sent (shadow)`. If you do not
   see that, stop and read `workflows/99-troubleshooting.md`.

3. **Fill in the property.**
   ```bash
   cp knowledge/property.example.md   knowledge/property.md
   cp knowledge/faq.example.md        knowledge/faq.md
   cp knowledge/disclosure.example.md knowledge/disclosure.md
   ```
   Edit `config/hotel.yaml` (name, address, contact, languages) and replace
   the Hotel Aurora content in `knowledge/*.md` with the real property.
   `disclosure.md` is the one-sentence AI-disclosure line appended to every
   WhatsApp/chat send (`Messaging.with_disclosure()`) - it carries the EU AI
   Act Article 50 line, see `docs/safety.md`.

4. **Build the vetted supplier book.** This is the guardrail the whole agent
   is built around - see `docs/safety.md`. Edit `config/vendors.yaml`:
   - Keep the four categories that apply to you (dining/chef, transport,
     tickets, entertainment) or add your own by copying an existing entry's
     shape.
   - Fill in each vendor's real contact email and your negotiated rate.
   - Add keywords a guest is actually likely to use ("cellist", "airport",
     "tasting menu") - `docs/how-it-works.md` explains how matching works.
   - Leave `rules.vendor_trusted_only: true` in `config/agent.yaml` unless you
     have a specific reason to let the agent draft to an unvetted fallback.

5. **Pick how the agent thinks.** `config/hotel.yaml`'s `llm.provider` starts
   as `interactive` - it asks you, in this Claude Code session, instead of
   calling a model. That costs nothing extra. The only thing that goes
   through it automatically is translating a guest-facing draft into their
   language - every booking decision is deterministic Python
   (`tools/engine.py`), never a model guess. `make report --narrate` is the
   one other, opt-in, model call. `docs/how-it-works.md` and `docs/safety.md`
   cover the other providers.

6. **Connect real channels (optional for now).** `systems.email.adapter` and
   `systems.messaging.adapter` in `config/hotel.yaml` start as `mock`, which
   only ever sees the bundled fixtures. `docs/integrations.md` covers `imap`,
   `gmail` and `unipile`. Run `make doctor` after changing either.

7. **Re-check.**
   ```bash
   make doctor
   ```
   Once the property name is real, `knowledge/property.md` exists, and
   `config/vendors.yaml` has real contacts, the relevant lines turn green.
   Move on to `workflows/10-new-requests.md` to run the loop for real.
