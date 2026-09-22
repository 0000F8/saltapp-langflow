# saltapp-langflow

Langflow custom components for [Salt](https://saltapp.ai): send an
end-to-end encrypted chat message, ask a human a question and wait for
the tap, request a payment, and poll for new Salt events -- all as
drop-in nodes in a Langflow flow, built on the
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
the four component classes, and the component files sit directly inside
it (no further nesting). So point Langflow's env var at this repo's root
(the *parent* of `saltapp_langflow/`):

```bash
export LANGFLOW_COMPONENTS_PATH=/path/to/saltapp-langflow
langflow run
```

Langflow will show a category named **saltapp_langflow** (Langflow uses
the folder's own name as the category label) in the component palette,
containing the four components below. If you want a nicer label, rename
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

Every component shares five credential/connection fields; the first three
are always required, the last two are never used by anything in this
package (see "Working keyless" below) but are kept on every component for
a consistent field layout:

| Field | Type | Required | Notes |
|---|---|---|---|
| `host` | text | yes | The Salt deployment, e.g. `https://saltapp.ai`. |
| `agent_id` | text | yes* | This agent's own Salt id. Salt Send Message and Salt Ask Human/Listen use it to tell this agent's own row apart from everyone else's (Send Message: skip encrypting a copy to yourself as a "recipient"; Ask Human/Listen: namespace the local poll-cursor file). Salt Request Payment accepts it too for a consistent field set but never sends it to the API. |
| `api_key` | secret | yes | This agent's Salt API key. |
| `private_key` (Agent Private Key) | secret, multi-line | no | Not needed by anything in this package. |
| `passphrase` (Private Key Passphrase) | secret | no | Not needed by anything in this package. |

### Salt Send Message

Encrypts `text` for every other member of `chat_id` (fetched live from
Salt) and posts it, plus a best-effort self-copy if this agent's own
public key is among the chat's members.

- Inputs: `chat_id` (tool mode), `text` (tool mode), `quiet` (advanced,
  default off).
- Output: `Result` (`Message`) -- a one-line confirmation; the sent-message
  id is in the component's status line.
- Raises if nobody else in the chat has a public key on file yet (nothing
  would be encrypted for).

### Salt Ask Human

Posts a card with button options into `chat_id` and waits, up to 50
seconds, for a human to tap one. `tool_mode` is on for `chat_id`,
`question`, and `options`, so an Agent component with a Tools input can
call this directly as a tool.

- Inputs: `chat_id` (tool mode), `question` (tool mode), `options` (tool
  mode, comma-separated labels, 1-5, default `"Yes,No"`), `wait_seconds`
  (default and hard cap 50 -- a value above 50 is silently clamped).
- Output: `Answer` (`Message`) -- the tapped option's label, or a plain
  `"No answer within <n>s."` on timeout. The component never raises on a
  timeout; a flow should not crash because a human did not tap in time.
  Who answered and the raw tapped `action_id` are in the status line.
- A tap from another Salt **agent** is ignored -- only a human's tap
  resolves the ask.
- Real API errors (bad api-key, unreachable host, and so on) still raise
  normally; only "nobody tapped in time" is reported as a plain result.

### Salt Request Payment

A thin wrapper over Salt's payment-request API: posts a real payment
request bubble into `chat_id`.

- Inputs: `chat_id`, `receiver_id`, `wallet_id`, `amount` (a human-decimal
  string like `"4.50"`, never base units), `message` (optional) -- all
  tool mode.
- Output: `Result` (`Data`) -- the raw API response (request id, status,
  and so on).

### Salt Trigger/Listen

Polls this agent's Salt updates for the next new event since the last
call, instead of Langflow's own built-in `Webhook` component. See "Why not
the built-in Webhook component" below for why.

- Inputs: `wait_seconds` (default 25, hard cap 55), `event_types`
  (optional, comma-separated filter: `message`, `card_interaction`,
  `invoice_paid`, `chat_opened`, `handoff_confirmed`, `handoff_received`;
  blank means any of them).
- Output: `Event` (`Data`) -- `{"status": "event", "event_type": ...,
  "delivery_id": ..., "created_at": ..., "body": ...}` for the event
  found, or `{"status": "no_new_events"}` if `wait_seconds` elapses first.
- Persists a small poll-cursor file per agent so a later run does not
  re-deliver an event an earlier run already returned (see AGENTS.md,
  "The ask/poll and listen-poll designs").
- Meant to sit at the start of a flow that is itself triggered on its own
  schedule, cron, or manual run, and pick up whatever is new since last
  time -- not to hold one HTTP request open for a long time.
- **Shares its poll cursor with Salt Ask Human, and shares its underlying
  "one ack per agent" with anything else polling this same Salt agent
  (salt-api keeps a single stored position per agent, not one per caller).
  Don't run Salt Ask Human and Salt Trigger/Listen concurrently against
  the same agent, and don't run two Ask Human calls concurrently on it
  either** -- see AGENTS.md's "File-based cursor state" for what actually
  goes wrong (a missed event, not just wasted work) and why this package
  has no way to enforce it for you.

## Working keyless

Every component in this package runs **without** `private_key`/
`passphrase` set at all:

- **Salt Send Message** only needs recipients' *public* keys to encrypt a
  message for them -- fetched live from the chat's member list. Sending
  never needs this agent's own private key.
- **Salt Ask Human** and **Salt Trigger/Listen** only ever read plain
  structured data: a button tap, a payment confirmation, a chat-opened
  notice. None of these arrive as PGP ciphertext, so there is nothing to
  decrypt -- a tapped button is never encrypted, even in a chat where
  every message is.
- **Salt Request Payment** posts a plain structured API record; no
  encryption is involved on either side.

`private_key`/`passphrase` are kept as optional fields on every component
purely for a consistent field layout across this package (and in case a
future component needs to read encrypted message text -- none of the four
here do).

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

**Salt Trigger/Listen** exists instead: it pulls from Salt's own
`GET /api/v1/agent/updates` socket-mode endpoint (the same one
`saltapp.socket.SocketClient` and Salt Ask Human's poll both use),
verifying each row's signature itself before it is ever returned. This is
also consistent with Langflow's own synchronous, run-to-completion
component-execution model: a flow run is not a persistent daemon by
default (unlike `saltapp.agent.Agent.run_socket()`), so both Ask Human and
Listen are built the same way -- as a bounded poll, not a long-lived
listener.

## A worked example flow

**"Ask before you spend"**: an Agent component with `saltapp-langflow`'s
Salt Send Message, Salt Ask Human, and Salt Request Payment wired in as
tools (Agent's Tools input accepts any component with a `tool_mode` input).
The LLM decides, from the conversation, that a purchase needs approval:
it calls Salt Ask Human with `question="Approve this $12 purchase?"` and
`options="Approve,Decline"`; a human in the chat taps a button within 50
seconds; the tool call returns `"Approve"` or `"Decline"` (or a timeout
message) straight into the agent's context, and the agent's next turn
calls Salt Request Payment (on approval) or Salt Send Message (on
decline) accordingly. No separate inbox, no polling loop in your flow's
own logic -- the wait is inside the tool call.

A second, separate flow can use **Salt Trigger/Listen** at its start,
triggered on a schedule (Langflow's own run scheduling, or an external
cron hitting the Langflow API), to pick up whatever happened on Salt since
its last run -- new messages, taps, payments -- and act on it.

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
```

See `AGENTS.md` for the design notes behind the poll-based Ask Human/
Listen components, and `HANDOFF.md` for what was tested, how to run a
manual UAT pass against a real `saltapp.ai` account, and what is left
undone.
