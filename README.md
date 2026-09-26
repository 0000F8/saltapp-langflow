# saltapp-langflow

Langflow custom components for [Salt](https://saltapp.ai): send a chat
message (encrypted, or plain in an open room), ask a human a question and
check for the tap, request a payment, read a room's messages on demand,
manage this agent's interests in a room, and check for new Salt events --
all as drop-in nodes in a Langflow flow, built on the
[`saltapp`](https://github.com/0000F8/saltapp-python) Python SDK.

## Install

`saltapp` is not yet on PyPI (it will replace the git dependency below once
it ships there), so install straight from source for now:

```bash
pip install "saltapp-langflow @ git+https://github.com/0000F8/saltapp-langflow@main"
```

or, working from a local checkout of this repo:

```bash
pip install -e .
```

This package assumes you already have `lfx` (or a full `langflow`
install, which bundles it) available -- see "Loading these components"
below. `lfx`/`langflow` is intentionally NOT a base runtime dependency of
this package (see `pyproject.toml`'s comment); it is only a `dev` extra
here, for this repo's own test suite.

## Loading these components into a real Langflow install

Langflow discovers custom components through the `LANGFLOW_COMPONENTS_PATH`
environment variable (verified against the current
[Create custom Python components](https://docs.langflow.org/components-custom-components)
doc, 2026-09-18). It treats each directory named there as a **base
directory** whose *immediate subfolders* are category folders -- each
category folder needs an `__init__.py`, and a component file may be at
most 2 levels deep from the base directory (`<base>/<category>/<file>.py`
is discovered; a further subfolder is not).

This repo's `saltapp_langflow/` folder is already laid out as exactly one
such category folder: it has an `__init__.py` that imports and re-exports
the six component classes, and the component files sit directly inside
it (no further nesting). So point Langflow's env var at this repo's root
(the *parent* of `saltapp_langflow/`):

```bash
export LANGFLOW_COMPONENTS_PATH=/path/to/saltapp-langflow
langflow run
```

Langflow will show a category named **saltapp_langflow** (Langflow uses
the folder's own name as the category label) in the component palette,
containing the six components below. If you want a nicer label, rename
or symlink the folder (e.g. `salt/`) before pointing
`LANGFLOW_COMPONENTS_PATH` at its parent -- the folder name is the only
thing that changes; nothing inside the files needs to change.

Docker (adapting the pattern from the same doc page):

```bash
docker run -d \
  --name langflow \
  -p 7860:7860 \
  -v /path/to/saltapp-langflow/saltapp_langflow:/app/custom_components/saltapp_langflow \
  -e LANGFLOW_COMPONENTS_PATH=/app/custom_components \
  langflowai/langflow:latest
```

`LANGFLOW_COMPONENTS_PATH` can hold more than one base directory
(pathsep-separated) if you already use it for other custom components --
just add this repo's root as one more entry.

## Components

Salt Send Message, Salt Ask Human, Salt Request Payment, and Salt Read
Updates share five credential/connection fields; the first three are
always required, the last two are never used by anything in this package
(see "Working keyless" below) but are kept on those four components for a
consistent field layout. Salt Read Room and Salt Interests, added in
0.2.0's open-rooms support, deliberately do not carry `private_key`/
`passphrase` at all -- see "Working keyless" below for why:

| Field | Type | Required | Notes |
|---|---|---|---|
| `host` | text | yes | The Salt deployment, e.g. `https://saltapp.ai`. |
| `agent_id` | text | yes* | This agent's own Salt id. **Salt Send Message** (required) uses it to tell its own row apart from everyone else's, so it can skip encrypting a copy to itself as a "recipient". **Salt Read Updates** (required) uses it to namespace its local check-cursor file. **Salt Ask Human** (optional, as of 2026-09-26) no longer uses it for anything -- it reads its own card directly rather than a per-agent cursor -- and keeps the field only for a consistent layout with the other components. Salt Request Payment and Salt Interests accept it too (optional) for the same consistent-field-set reason, and never send it to the API. Salt Read Room has no `agent_id` field at all -- it never needs to tell itself apart from anyone. |
| `api_key` | secret | yes** | This agent's Salt API key. **Optional on Salt Read Room only** -- see "Open rooms and Salt Read Room's anonymous read" below. |
| `private_key` (Agent Private Key) | secret, multi-line | no | Not needed by anything in this package. Present only on Salt Send Message, Salt Ask Human, Salt Request Payment, Salt Read Updates. |
| `passphrase` (Private Key Passphrase) | secret | no | Not needed by anything in this package. Present only on Salt Send Message, Salt Ask Human, Salt Request Payment, Salt Read Updates. |

### Salt Send Message

Posts `text` into `chat_id` -- plain text if the room is open
(`encrypted: false`, saltapp 0.2.0), otherwise encrypted for every other
member of the chat (fetched live from Salt), plus a best-effort self-copy
if this agent's own public key is among the chat's members.

- Inputs: `chat_id` (tool mode), `text` (tool mode), `quiet` (advanced,
  default off).
- Output: `Result` (`Message`) -- a one-line confirmation; the status line
  says whether it went out plain or encrypted, the sent-message id, and
  (when the API returns it) why an open room actually delivered it
  (`delivered_because`: mention, reply, keyword, or "all").
- Raises if the room is encrypted and nobody else in the chat has a
  public key on file yet (nothing would be encrypted for). An open room
  never raises for this reason -- there is nothing to encrypt.

### Salt Ask Human

Posts a card with button options into `chat_id`, then checks **once** for
a tap and always returns -- the answer if one is already there, or a
pending result you re-check later. There is no waiting inside this
component; see "Push vs. on-demand" below for why, and how to actually
get an answer.

- Inputs: `chat_id` (tool mode), `question` (tool mode, only used when
  posting), `options` (tool mode, comma-separated labels, 1-5, default
  `"Yes,No"`, only used when posting), `card_id` (tool mode, optional --
  leave blank to post a new question; fill in a card_id from a previous
  pending result to check that same card again without posting a new
  one).
- Output: `Answer` (`Message`) -- the tapped option's label if the
  one-shot check found a tap (this covers the instant-tap case even on a
  fresh post), or `"pending:<card_id>"` otherwise. **This is a breaking
  behavior change**: earlier versions of this component blocked for up to
  50 seconds (`wait_seconds`, now removed) before returning a
  `"No answer within <n>s."` result. There is no more waiting -- a flow
  or an Agent-driven LLM must re-invoke this component with `card_id` set
  to the pending id whenever it wants to check again. Who answered and the
  raw tapped `action_id` are in the status line.
- A tap from another Salt **agent**, or from someone no longer a member of
  the chat, is ignored -- only a current human chat member's tap resolves
  the ask.
- **Checks its own card, not this agent's shared outbox (2026-09-26).**
  Earlier versions of this component checked the same per-agent
  socket-mode outbox Salt Read Updates uses, which meant two concurrent
  Ask Human calls (or an Ask Human call racing a Read Updates check) on
  the same agent could silently consume each other's answers. It now
  reads the tapped card's own interaction log (`GET /api/v1/cards/:id`)
  instead, which is scoped to that one card and shares nothing with any
  other check -- **run as many Salt Ask Human calls concurrently against
  the same agent as you like.** (Salt Read Updates keeps its own
  "one poller per agent" caveat -- see its section below.)
- A 429 from Salt's own rate limit degrades to the same pending result a
  real "nobody tapped yet" would, with a wait hint in the status line,
  rather than raising.
- Real API errors (bad api-key, unreachable host, and so on) still raise
  normally; only "nobody tapped yet" (including a rate limit) is reported
  as a plain pending result.

### Salt Request Payment

A thin wrapper over Salt's payment-request API: posts a real payment
request bubble into `chat_id`.

- Inputs: `chat_id`, `receiver_id`, `wallet_id`, `amount` (a human-decimal
  string like `"4.50"`, never base units), `message` (optional) -- all
  tool mode.
- Output: `Result` (`Data`) -- the raw API response (request id, status,
  and so on).

### Salt Read Room

Reads a chat's session and messages **on demand** -- `GET
/api/v1/chats/:id` (saltapp 0.2.0), optionally with a `last` cursor for a
catch-up window instead of the ten most recent. Every returned message
already carries `encrypted`/`delivered_because` verbatim from salt-api;
this component passes them through as-is.

- Inputs: `chat_id` (tool mode), `last` (optional -- a message `seq`
  cursor; blank means the ten most recent messages).
- Output: `Room` (`Data`) -- the raw `{"session": {...}, "messages":
  [...]}` payload `get_chat` returns.
- **Works fully keyless**: `api_key` is optional here (unlike every other
  component in this package) -- see "Open rooms and Salt Read Room's
  anonymous read" below.
- Has no `agent_id`/`private_key`/`passphrase` fields at all -- see
  "Working keyless" below.

### Salt Interests

Sets, reads, or clears this agent's follow settings for a chat (saltapp
0.2.0's subscriptions): who gets notified of a new message in a room
nobody @mentioned or replied to them in.

- Inputs: `chat_id` (tool mode), `action` (tool mode, dropdown: `"set"`
  default, `"get"`, `"clear"`), `mode` (tool mode, dropdown: `"addressed"`
  -- today's unwritten default, mentions/replies only --, `"keywords"`,
  `"all"`; required when `action="set"`), `keywords` (tool mode, optional
  comma-separated list, only used when `mode="keywords"`).
- Output: `Result` (`Data`) -- the raw API response for `"get"`/`"set"`,
  or `{"status": "error", "error": "..."}` (never a raised exception) for
  an invalid `action` or a missing `mode` when `action="set"`.
- salt-api itself refuses this against an encrypted chat (422); this
  component does not pre-check that, it relays whatever the server says.

### Salt Read Updates

*(was Salt Trigger/Listen -- renamed in this same pass, see "No polling,
ever" below.)* Checks **once** for this agent's new Salt events (message,
card tap, payment, chat opened, or hand-off) since the last check,
instead of Langflow's own built-in `Webhook` component. See "Why not the
built-in Webhook component" below for why.

- Inputs: `event_types` (optional, comma-separated filter: `message`,
  `card_interaction`, `invoice_paid`, `chat_opened`, `handoff_confirmed`,
  `handoff_received`; blank means any of them).
- Output: `Events` (`Data`) -- `{"status": "events", "events": [{
  "event_type": ..., "delivery_id": ..., "created_at": ..., "body": ...
  }, ...]}` for every matching event found in that one check (not just
  the first), or `{"status": "no_new_events", "events": []}` if none
  were. **This is a breaking behavior change**: earlier versions of this
  component (as Salt Trigger/Listen) polled for up to `wait_seconds`
  (default 25, hard cap 55, now removed) before giving up, and returned
  at most one event. There is no more waiting, and multiple events in the
  same check are no longer dropped.
- Persists a small check-cursor file per agent so a later run does not
  re-deliver an event an earlier run already returned (see AGENTS.md,
  "The ask/check design").
- Meant to sit at the start of a flow that is itself triggered on its own
  schedule, cron, or manual run, and pick up whatever is new since last
  time.
- **One poller per agent.** This is now the ONLY component in this
  package that reads this agent's shared socket-mode outbox (Salt Ask
  Human moved to checking its own card directly in 2026-09-26 -- see its
  own section above -- and no longer shares this cursor). salt-api still
  keeps a single stored position per agent, not one per caller, so
  running two Salt Read Updates checks concurrently against the same
  agent (or one beside any other consumer of that agent's outbox, such as
  a socket-mode `Agent`) can still resolve rows out from under one
  another -- see AGENTS.md's "File-based cursor state" for what actually
  goes wrong (a missed event, not just wasted work) and why this package
  has no way to enforce it for you.

### Open rooms and Salt Read Room's anonymous read

saltapp 0.2.0 adds "open rooms": a chat with `session.encrypted == false`
carries plain text, not PGP ciphertext, and -- if it is also `public` --
can be read with **no Salt identity at all**. Salt Read Room's `api_key`
field is optional specifically to reach that path: leave it blank and
this component sends no api-key header whatsoever, which salt-api
answers for a `public && !encrypted` room exactly as it would for an
authenticated read (same session + messages shape). Anything else with a
blank key -- a private room, an encrypted room, or a room that does not
exist -- still 404s, indistinguishably from a genuine not-found. Salt
Send Message, when it posts into an open room, uses the SAME
`session.encrypted` flag to decide plain vs. encrypted delivery
automatically; you don't tell it which to use.

## Working keyless

Every component in this package runs **without** `private_key`/
`passphrase` set at all:

- **Salt Send Message** only needs recipients' *public* keys to encrypt a
  message for them -- fetched live from the chat's member list -- or, in
  an open room, no keys at all. Sending never needs this agent's own
  private key.
- **Salt Ask Human** and **Salt Read Updates** only ever read plain
  structured data: a button tap, a payment confirmation, a chat-opened
  notice. None of these arrive as PGP ciphertext, so there is nothing to
  decrypt -- a tapped button is never encrypted, even in a chat where
  every message is.
- **Salt Request Payment** posts a plain structured API record; no
  encryption is involved on either side.
- **Salt Read Room** returns a chat's raw messages exactly as sent
  (ciphertext included, for an encrypted room) and never decrypts
  anything, and **Salt Interests** never touches message content at all
  -- a subscription is a plain per-agent setting.

`private_key`/`passphrase` are kept as optional fields on Salt Send
Message, Salt Ask Human, Salt Request Payment, and Salt Read Updates
purely for a consistent field layout across those four (and in case a
future component needs to read encrypted message text -- none of these
four do either). Salt Read Room and Salt Interests, added alongside open
rooms, skip the fields entirely: neither has a plausible future need for
them, so there is no present-but-unused field worth keeping for
consistency's sake (see AGENTS.md, "The keyless boundary").

## Why not the built-in Webhook component

Langflow ships a built-in `Webhook` component
(`src/lfx/src/lfx/components/input_output/webhook.py`,
[docs](https://docs.langflow.org/component-webhook)) that lets an external
`POST https://<host>/api/v1/webhook/<flow_id>` trigger a flow run. We read
its actual source before deciding: `WebhookComponent.build_data` builds its
output from `self.data` alone -- the component receives **no HTTP request
headers at all**, by design.

Salt signs every webhook/socket-mode delivery with an
`X-Salt-Signature: t=<unix>,v1=<hex>` HMAC header
(`saltapp.webhook.verify_signature`), and verifying it is mandatory before
trusting the body: an unverified `card_interaction` or `invoice_paid`
payload is plaintext and directly actionable (it can look exactly like a
real payment confirmation or a real button tap). Since the built-in
Webhook component structurally cannot see that header, wiring Salt's
webhook callback straight at a Langflow flow's `/api/v1/webhook/<flow_id>`
endpoint would mean trusting unverified, unauthenticated input that merely
*claims* to be from Salt. We are not willing to ship that.

**Salt Read Updates** exists instead: it checks Salt's own
`GET /api/v1/agent/updates` socket-mode endpoint, verifying each row's
signature itself before it is ever returned. (Salt Ask Human's own
one-shot check used to read this same endpoint too; as of 2026-09-26 it
reads its own card instead -- see its section above and "One poller per
agent, still" below.) This is also consistent with Langflow's own
synchronous, run-to-completion component-execution model: a flow run is
not a persistent daemon by default (unlike
`saltapp.agent.Agent.run_socket()`), so both Ask Human and Read Updates
are built the same way -- a single on-demand check, not a long-lived
listener, and (as of this package's 2026-09-22 rewrite) not even a
bounded poll loop. See "No polling, ever" below.

## No polling, ever

This package has one hard rule, from the product owner: **Langflow
components are stateless calls, and must never loop or sleep waiting for
something to happen.** Every earlier version of this package's Salt Ask
Human and Salt Trigger/Listen (now Salt Read Updates) DID loop -- a
bounded short-poll, sleeping between rounds for up to `wait_seconds`.
That loop is gone from both. `_salt_common.check_for_event` (Salt Read
Updates' own check, and Salt Ask Human's too until 2026-09-26) makes
exactly ONE `GET /api/v1/agent/updates` call per component invocation
(the `timeout=2` it sends is the server's own short grace window for
that one request, not a client-side retry budget) and returns
immediately with whatever it finds; Salt Ask Human's check (rewritten
2026-09-26 for a different reason -- see its own section and "One
poller per agent, still" below) is the same shape, one
`GET /api/v1/cards/:id` call and an immediate return, just against a
different endpoint. See AGENTS.md's "The ask/check design" for the full
mechanics. This is a real, breaking behavior change to both components'
public API: see their sections above ("This is a breaking behavior
change").

## One poller per agent, still

The 2026-09-22 "no polling, ever" rewrite (above) made both Salt Ask
Human and Salt Read Updates single-shot checks, but for a while both
checked the SAME per-agent socket-mode outbox (`GET
/api/v1/agent/updates`), which keeps exactly ONE forward-only cursor per
agent server-side, not one per caller or purpose. That meant two
concurrent checks against one agent -- two Ask Human calls, or an Ask
Human call racing a Read Updates check -- could resolve rows out from
under one another, silently. As of 2026-09-26, **Salt Ask Human no
longer has this problem at all**: it reads the tapped card's own
interaction log (`GET /api/v1/cards/:id`) instead, which is scoped to
that one card and idempotent -- run as many concurrent Ask Human calls
against the same agent as you like. **Salt Read Updates still has this
constraint**, because reading "everything new for this agent" has no
narrower a scope to poll than the shared outbox itself: don't run two
Salt Read Updates checks (or a Read Updates check beside any other
consumer of that agent's updates, such as a socket-mode `Agent`)
concurrently against the same agent_id.

## Push vs. on-demand

Genuine push -- "notify me the moment something happens, with no polling
and no re-checking" -- needs something that can hold a persistent
connection open (a webhook receiver, or a real-time socket). **Langflow
has no mechanism for a custom component to register an HTTP endpoint or
run a persistent process**: a component's build method runs once per flow
invocation and returns, full stop. So genuine push cannot be built INSIDE
this package, at all, ever -- it needs pairing with something else that
CAN hold a persistent receiver:

- **`saltapp.agent.Agent`** (or `saltapp.integrations.create_asgi_app`)
  run as their own small always-on process.
- **`salt-claude-agent`**, if your setup already runs one.
- **`n8n-nodes-saltapp`'s `Salt Trigger` node**, if your setup already
  runs n8n -- n8n's own workflow engine holds the persistent connection,
  not a Langflow component.

Whichever of those receives the push still needs its own way to actually
start a Langflow flow run (Langflow's own API/webhook-triggered flow
execution) -- that wiring is outside this package's scope; we are not
attempting to build a persistent receiver inside `saltapp-langflow`
itself, because that would contradict the stateless-call architecture
described above.

**This package's components remain the correct ON-DEMAND-READ half of
that pairing, not a replacement for the push half.** Salt Read Room, Salt
Read Updates, and Salt Ask Human's re-check path are all "read what's new
right now, because I was just invoked" (Read Updates from a cursor; Ask
Human by re-reading its own card fresh, which needs no cursor at all) --
call them again whenever your flow (or the LLM driving it) next wants to
check.

## A worked example flow

**"Ask before you spend"**: an Agent component with `saltapp-langflow`'s
Salt Send Message, Salt Ask Human, and Salt Request Payment wired in as
tools (Agent's Tools input accepts any component with a `tool_mode`
input). The LLM decides, from the conversation, that a purchase needs
approval: it calls Salt Ask Human with `question="Approve this $12
purchase?"` and `options="Approve,Decline"`. If a human happens to tap
within that one check, the tool call returns `"Approve"` or `"Decline"`
straight into the agent's context and the agent's next turn calls Salt
Request Payment (on approval) or Salt Send Message (on decline)
accordingly. Otherwise it returns `"pending:<card_id>"`; the LLM (or your
flow's own retry logic) re-invokes Salt Ask Human later with that
`card_id` to check again -- there is no waiting inside the tool call
itself.

A second, separate flow can use **Salt Read Updates** at its start,
triggered on a schedule (Langflow's own run scheduling, or an external
cron hitting the Langflow API), to pick up whatever happened on Salt since
its last run -- new messages, taps, payments -- and act on it. A third
flow can use **Salt Read Room** the same way against a public open room
with no `api_key` at all, to summarize what was said since the last
`last` cursor it saw.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

See `AGENTS.md` for the design notes behind the check-based Ask Human/
Read Updates components, and `HANDOFF.md` for what was tested, how to run
a manual UAT pass against a real `saltapp.ai` account, and what is left
undone.
