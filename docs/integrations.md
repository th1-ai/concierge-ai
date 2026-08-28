# Connecting your systems

Every connector in this repo is one of three things, and the table says which.
We will not tell you an integration exists when it does not.

| Badge | Means |
|---|---|
| **built** | Written against the real API and tested against it. |
| **universal** | Works with any system through a common protocol: IMAP/SMTP, CSV, a webhook. |
| **stub** | Interface only. Calling it raises a clear error with a recipe for adding it. |

Check what is actually working on your machine at any time:

```bash
make doctor
```

Concierge AI uses three adapter families: PMS (find the guest, log the
booking), Email (both the intake channel and the vendor channel - every
vendor in `config/vendors.yaml` is contacted by email), and Messaging
(WhatsApp intake and, for a WhatsApp-sourced guest, the confirmation). Sheets
and the stub families (POS, Accounting, Reviews, Calendar, Payments,
Procurement, Locks, Courier) are not used by this agent.

## Status

### PMS - `systems.pms.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/hotel/*.json`. What `make demo` uses. |
| `csv` | universal | a CSV export | Reads `data/imports/*.csv`. **Start here.** Works with every PMS. |
| `cloudbeds` | built | OAuth app + refresh token | Live reads and writes. |
| `cli` | universal | a JSON-speaking CLI | Advanced. Bridges to a vendor command line tool. |

Concierge AI calls `find_guest()` and `list_reservations()` to fill in a
guest's name, room and reservation when an inbound message does not already
carry them, and `add_note()` (a guarded write) to log a confirmed booking
against the reservation once the loop closes - see `docs/how-it-works.md`.
Neither is required: every fixture and every manual `tools/request.py add`
already supplies the guest details directly, so the demo works with `mock`
and no PMS lookup ever fires.

**`csv` - the one that always works.** Export from your PMS and drop the
files in `data/imports/`:

- `reservations.csv` - `id, status, check_in, check_out, room_type_id,
  room_type_name, room_id, adults, children, source, total, balance, currency,
  guest_email, guest_first_name, guest_last_name, guest_phone, guest_country`
- `guests.csv` - `id, first_name, last_name, email, phone, country, language, vip`
- `rooms.csv` - `id, name, max_occupancy, count, rank`
- `rates.csv` - `date, room_type_id, price, currency, min_los, available, closed`

In CSV mode `add_note()` cannot write back to your PMS, so it appends to
`data/exports/pms_writes.csv` with everything a person needs to apply it by
hand - the same honest behaviour as `set_rate`/`update_reservation` in every
other repo in this family.

**`cloudbeds`.** Create an app in the Cloudbeds developer portal, authorise it
once against your property, and put the result in `.env`:

```
CLOUDBEDS_CLIENT_ID=
CLOUDBEDS_CLIENT_SECRET=
CLOUDBEDS_REFRESH_TOKEN=
CLOUDBEDS_PROPERTY_ID=
```

Scopes: `read:reservation`, `write:reservation`, `read:guest`, `read:room`.

### Email - `systems.email.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/*.json`. |
| `imap` | universal | mailbox + app password | Any provider. **Start here.** |
| `gmail` | built | Google OAuth desktop client | Adds Gmail labels and threads. |

This is the channel every vendor is contacted through, and one of the two
guest intake channels. Point a concierge-dedicated mailbox at it (not the
same inbox Front Desk AI reads, if you run both) - every unread message in
this mailbox is treated as a new request unless it lands on a thread the
agent is already chasing.

**`imap`.** In `.env`:

```
EMAIL_ADDRESS=concierge@example.com
EMAIL_PASSWORD=            # an APP password, never your login password
IMAP_HOST=imap.example.com
SMTP_HOST=smtp.example.com
SMTP_PORT=587
```

**`gmail`.** Google Cloud Console: enable the Gmail API, configure the
consent screen, create an OAuth client of type **Desktop app**, download the
JSON to `credentials.json`, then `pip install google-api-python-client
google-auth-oauthlib` and run `make doctor`.

### Messaging - `systems.messaging.adapter`

| Adapter | Status | Needs | Notes |
|---|---|---|---|
| `mock` | universal | nothing | Reads `fixtures/inbound/messages.json`. |
| `unipile` | built | your own UniPile account | WhatsApp on your own number. |
| `webhook` | universal | any URL | POST to Zapier, Make, n8n, or your own endpoint. |

**`unipile`.** You create the account, you connect your number by QR code, you
own the credentials: `UNIPILE_DSN`, `UNIPILE_API_KEY`, `UNIPILE_ACCOUNT_ID`.
WhatsApp Business policy limits what you may send outside a guest-initiated
window; read your provider's rules before turning this on.

**`webhook`.** Send-only: sets `MESSAGING_WEBHOOK_URL` and the agent POSTs
`{chat_id, text, kind, hotel, sent_at}`. Fine for the guest-facing side, but
this agent also needs to *read* WhatsApp replies (a guest's budget
approval) - `webhook` alone cannot do that. Use `unipile` if WhatsApp intake
matters to you.

### Everything else

`pos`, `accounting`, `reviews`, `calendar`, `payments`, `procurement`, `locks`
and `courier` are **stubs** - the interface exists, nothing is implemented,
and none of them are used by Concierge AI. If a future version of this agent
needs one (a real calendar invite for the vendor, say), the recipe below
applies unchanged.

## Implement your own

<a id="implement-your-own"></a>

The interface is small on purpose, and your Claude Code session can do this
with you in an afternoon. Open `claude` in this folder and paste:

> Read `docs/integrations.md#implement-your-own` and `core/adapters/base.py`.
> I need a Calendar adapter for **<your system>**. Its API docs are at
> **<url>** and I have credentials in `.env` as `<VAR names>`. Copy
> `core/adapters/pms_csv.py` as the shape, implement `ping`, `capabilities`
> and the read methods first, register it in `core/adapters/__init__.py`, and
> stop before the write methods so I can check the reads with `make doctor`.

### The five steps

**1. Copy the closest existing adapter.** `core/adapters/pms_csv.py` for a
PMS, `email_imap.py` for a mailbox, `messaging_webhook.py` for a chat
channel, `domain_stub.py` for a system family with no built example yet.

**2. Implement `ping()` and `capabilities()` first.**

```python
def ping(self) -> HealthCheck:
    """Never raises. Returns ok=False with a fix_hint a hotel can act on."""

def capabilities(self) -> set[str]:
    """The method names that actually do something on this adapter."""
```

**3. Implement the reads**, mapping the vendor's fields onto the dataclasses
in `core/adapters/base.py`. Put anything you do not map into `.extra` rather
than dropping it.

**4. Implement the writes, each with the guard.**

```python
from core.adapters.base import guarded_write

@guarded_write("calendar_write")
def create_event(self, event: dict) -> dict:
    ...
```

The decorator is not optional - without it, an adapter can write while the
agent is in shadow mode, which defeats the entire safety model.

**5. Register it** in `core/adapters/__init__.py`'s `REGISTRY`, set
`systems.<family>.adapter: yoursystem` in `config/hotel.yaml`, and run
`make doctor`.

### Rules that matter

- **`ping()` never raises.**
- **Every write is decorated.** No exceptions.
- **Never log a credential.**
- **Redact on ingestion.** Any guest-written text goes through
  `core.redact.redact()` before it is stored, logged, or put in a model
  prompt.
- **Write a test.** Copy `tests/test_core_adapters_mock_csv.py` - feed your
  parser a fixture, check the dataclass that comes out, no network.

### `core/` is shared

`core/` is identical in all 28 agents in this family. A hotel-specific tweak
belongs in `tools/` or in your own adapter file, never in `core/`.
