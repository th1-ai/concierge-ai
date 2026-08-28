<!--
Copy this to knowledge/signature.md. Every message tools/engine.py drafts is
signed "Thank you,\nConcierge desk — <hotel.yaml's hotel.name>" - the hotel
name already comes from your own config, nothing to edit there. If you want a
different salutation than "Concierge desk", change the literal string in
`_signature()` at the top of tools/engine.py; it is one line.

This file's real job is the AI-disclosure line below (docs/safety.md, EU AI
Act Article 50). It is not wired into the drafts automatically - copy the
line you want into the outgoing mailbox's own signature block, or ask your
Claude session to add it to tools/engine.py's `_signature()` for you.
-->

This message was prepared with AI assistance and reviewed by our team.
Reply any time to reach a person directly.
