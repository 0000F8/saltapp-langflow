# AGENTS.md

Design notes for whoever works on this repo next.

## What this is

Six Langflow custom components (`saltapp_langflow/`) exposing a slice of
[Salt](https://saltapp.ai)'s agent actions inside a Langflow flow, built
on the [`saltapp`](https://github.com/0000F8/saltapp-python) Python SDK:
`SaltSendMessageComponent`, `SaltAskHumanComponent`,
`SaltRequestPaymentComponent`, `SaltReadUpdatesComponent` (was
`SaltListenComponent`), `SaltReadRoomComponent`, `SaltInterestsComponent`.
The last two, and the rename of the third, came from saltapp-python
0.2.0's "open rooms" (`lane/open-rooms`, 2026-09-22): a room can now be
`encrypted: false`, readable and postable in plain text, optionally with
no api-key at all. A sibling Dify plugin build covers a larger, 7-tool
surface (`get_answer`, `send_invoice`, `post_card`, `get_payment_status`,
and so on) against the same SDK -- this repo is deliberately smaller. Do
not grow this repo's tool list to match that one; Salt Read Room and Salt
Interests were added because saltapp-python 0.2.0 changed the underlying
contract this package wraps, not as a default -- if a real need shows up
for one of the Dify plugin's other actions here, that is a separate,
deliberate decision.

## Why no `Identity`/`Agent`/`saltapp.integrations` classes

`saltapp.identity.Identity` and `saltapp.agent.Agent` (and everything
under `saltapp.integrations.*`) are built around a **persistent**
process: one that holds a long-lived identity object, runs an event loop,
and dispatches webhook/socket events to `@agent.on_message`-style
handlers for as long as the process lives. A Langflow custom component's
`build`/output method runs exactly once per flow invocation and then
returns -- there is no long-lived process behind it to hand an `Identity`
or an `Agent` to. So every component here calls `saltapp.client.SaltClient`
methods directly with plain strings pulled straight from the component's
own field values (`self.api_key`, `self.chat_id`, ...) -- see
`_salt_common.py`. This is the same reasoning `saltapp-python`'s own
`AGENTS.md` gives for why the Langflow-adjacent "framework integrations"
layer (`saltapp.integrations.*`) is the wrong abstraction level for a
per-run component: that layer wraps Salt's six actions as tools for a
handful of *agent frameworks* (LangChain, CrewAI, ...), but a Langflow
custom component is not hosted inside one of those frameworks' own
runtimes -- it *is* the framework surface here.

## Why `chat_id` is an explicit input on every component

There is no ambient "current chat" a Langflow flow could read from -- a
flow is a graph of components wired together by the person who built it,
not a chat session. Every component that needs a chat takes `chat_id` as
a plain field, `tool_mode=True` where an LLM (via an Agent component)
should be the one supplying it. This mirrors how the Dify plugin's tools
are shaped, and how `saltapp.client.SaltClient`'s own methods are shaped
(every method takes the ids it needs, not an ambient "current" anything).

## The ask/check design (rewritten 2026-09-22 -- no polling, ever)

**The owner's explicit rule, via the task coordinator: "DO NOT USE
POLLING as a mechanic EVER. Langflow components are stateless calls: they
must never loop or sleep. Receiving is by webhook (Salt's POST is the
wake-up) where the plugin registers an endpoint, and otherwise reads are
ON DEMAND with a cursor when the tool/component is invoked."** This
package cannot register a webhook endpoint (see "Why not the built-in
Webhook component" in README.md, and "genuine push" below), so every
component that needs to know "did anything happen" does exactly ONE
on-demand check per invocation and returns immediately -- never a loop,
never a `time.sleep`, never a wait budget of any kind.

`_salt_common.check_for_event` is that one check:

1. Call `GET /api/v1/agent/updates?after=<cursor>&timeout=2` (the K2
   socket-mode contract) -- ONE HTTP request. `timeout=2` here is the
   server's own short grace window for that single request, not a
   client-side retry loop; nothing in this module calls this endpoint a
   second time on its own.
2. For each row in the response, verify its `X-Salt-Signature` header at
   `POLL_SIGNATURE_TOLERANCE_SECONDS` (see that constant's own comment
   for why this is the standard ~300s webhook tolerance, not the wider
   one some other saltapp-python callers still use), classify it into an
   `Event`, and check the caller's `predicate`.
3. Advance the (local) cursor past every row seen this round (verified or
   not, matched or not) and persist it via `cursor.set(...)` before
   returning, so a LATER call never re-processes what this call already
   consumed.
4. Return `(matches, new_cursor)` -- every event that matched `predicate`
   (or every verified event, if `predicate` is `None`), as a list. Unlike
   the old `poll_for_event` (removed in this pass), there is no "keep
   waiting" branch, so there is no reason to stop at the first match.

A row that fails signature verification is skipped, not fatal -- a
single on-demand call has no "next round" to retry it in, so treating an
unverifiable row as fatal would just make the NEXT call fail outright
after any one bad row, instead of skipping past it once, as before.

**Salt Ask Human** posts a card (unless `card_id` is already given, in
which case it skips posting -- a re-check call) and then runs exactly one
`check_for_event`, predicate: a `card_interaction` whose `card_id`
matches, tapped by a non-agent user. Either way it ALWAYS returns: the
answer if the one-shot check found it (covers the instant-tap case), or a
pending result (`Message(text=f"pending:{card_id}")`) otherwise. There is
no more `wait_seconds` -- "wait for the answer" is now the CALLER's job:
re-invoke this same component with `card_id` set to what the pending
result returned, whenever it next wants to check. An Agent-driven flow
does this naturally (the LLM sees `"pending:<id>"`, and can be prompted
to call the tool again later with that id); a scheduled/cron flow does it
by construction (its next scheduled run passes the same `card_id`).

**Salt Read Updates** (was Salt Trigger/Listen) runs exactly one
`check_for_event` with a predicate matching the caller's optional
`event_types` filter (or any classified type at all, if blank) and
returns every matching event from that one response page as a list --
also unlike the old design, which returned at most one event and made
the caller re-run the whole component just to see if there was a second
one already sitting in the same page.

## Genuine push is out of scope for this package

Langflow has no mechanism for a custom component to register an HTTP
endpoint or run a persistent process -- a component's build method runs
once per flow invocation and returns (see "Why no `Identity`/`Agent`"
above). So "receiving is by webhook" cannot be built INSIDE this package;
it needs pairing with something that CAN hold a persistent webhook
receiver:

- `saltapp.agent.Agent`/`saltapp.integrations.create_asgi_app` run as
  their own small always-on process, or
- `salt-claude-agent`, or
- the `n8n-nodes-saltapp` package's `Salt Trigger` node (n8n's own
  workflow engine holds the persistent connection, not a Langflow
  component).

Whichever of those receives the webhook still needs its own way to kick
off a Langflow flow run (Langflow's own API/webhook-triggered flow
execution) -- that wiring is out of this package's scope. The components
in this package remain the correct ON-DEMAND-READ half of that pairing
(check what happened since a cursor, right now, when invoked), not a
replacement for the push half. See README.md's "Push vs. on-demand"
section for the user-facing version of this.

## File-based cursor state

`_salt_common.PersistentCursor` is a one-integer JSON file under
`~/.salt/agents/<agent_id>/langflow/<purpose>/cursor.json` (override root
via `SALTAPP_LANGFLOW_STATE_DIR`, which the test suite uses to avoid
touching a real home directory). This lives in the same `~/.salt/agents/`
tree `saltapp.socket`'s own file-backed cursor/dedupe stores use, but
under a `langflow/` subdirectory specifically so this package's cursor
files never collide with a socket-mode `Agent`'s own files for the same
agent id, if both happen to run against the same account on the same
machine.

**`<purpose>` is `sc.SHARED_POLL_PURPOSE` ("poll") for BOTH Salt Ask Human
and Salt Read Updates (fixed 2026-09-22, was two separate purposes,
`"ask_human"`/`"listen"`; the constant keeps its old name across the same
day's later "no polling, ever" rewrite -- it still names the one shared
cursor file, even though nothing in this module polls with it anymore).**
They must share one file, not "never share," because salt-api keeps
exactly ONE ack per agent (`users.agent_updates_acked_id`), not one per
local purpose string: a check with `after=X` moves the server's stored
ack to `max(current_ack, X)`, and every subsequent check -- from ANY
caller, local file or not -- gets served from that resolved position. Two
separate local cursor files used to imply two independent positions that
don't actually exist server-side; if one component's check advanced the
real (shared) ack past rows the OTHER component's own, staler local file
still expected to see, that request got silently resolved past them and
never saw them at all -- not a "wastes time re-scanning" problem, a
genuine missed-event bug. Sharing one file makes this package's own
bookkeeping match the one true position the server already enforces.

**This does NOT make it safe to run Salt Ask Human and Salt Read Updates
concurrently against the same agent_id** (nor two `Ask Human` calls
concurrently, e.g. two parallel flow runs checking on two different
questions on the same agent). The underlying constraint is "one caller at
a time per agent" -- sharing the cursor file removes a MISLEADING
appearance of independence, it does not add mutual exclusion. There is no
lock here (a Langflow component has no natural place to hold one across
concurrent flow executions); if your deployment genuinely needs
concurrent on-demand checks against one Salt agent, serialize it at the
flow-orchestration level, not inside this package.

This is otherwise a **performance optimization, not a correctness
requirement** in the ordinary (single-caller) case: every `check_for_event`
call matches on something specific (a card_id, a requested event type),
so re-scanning old history would just waste time, never produce a wrong
answer. A missing or corrupt cursor file is treated as "start from 0" and
the next check just re-scans -- never a hard failure.

## The keyless boundary, and why even Ask Human needs no private key

Every component's `private_key`/`passphrase` fields are optional, and
nothing in this package actually uses them:

- Sending a message needs recipients' **public** keys only (fetched live
  from the chat's member list -- see `_salt_common.send_message`) -- a
  sender never needs its own private key to encrypt *to* someone else.
- A card tap, a payment confirmation, a chat-opened notice, a hand-off
  notice -- every event `_salt_common.check_for_event` can return -- arrive
  as **plain structured JSON**, never PGP ciphertext. Salt's own card
  protocol is deliberately unencrypted metadata (who tapped what, when);
  only message *bodies* are end-to-end encrypted. So even Salt Ask Human,
  whose whole job is "read the human's answer", never needs to decrypt
  anything -- there is nothing encrypted to decrypt in a button tap.
- A payment request is a plain API record on the TransferRequest rail; no
  PGP involved at either end.

If a future component in this package needs to read encrypted message
*text* (for example, a "decrypt and summarize this chat" component),
that is the first place `private_key`/`passphrase` would actually get
used, via `saltapp.crypto.decrypt`. Keep the field present-but-unused
pattern on **Salt Send Message**, **Salt Ask Human**, **Salt Request
Payment**, and **Salt Read Updates** until that day rather than removing
the fields now -- removing them would mean a different field layout
across those four components for no present benefit.

**Salt Read Room and Salt Interests (added 2026-09-22) deliberately break
that pattern and carry no `private_key`/`passphrase` fields at all.**
This is not an oversight -- both are structurally incapable of ever
decrypting anything, for a stronger reason than "unused today": Salt Read
Room returns a room's raw messages exactly as the server sent them
(ciphertext included, for an encrypted room) without touching PGP at any
point, and Salt Interests only ever reads or writes a per-agent
subscription setting, never message content. Neither has a plausible
future need for these fields the way, say, a future "read the last
message" component might -- so there is no present-but-unused field to
keep. Salt Read Room's `api_key` is optional too (see "Open rooms and
Salt Read Room's anonymous read" in README.md): it is the one field in
this whole package that is genuinely, not just conventionally, optional.

## Known limitations

- **No live end-to-end test against a running salt-api.** Every test in
  `tests/` mocks `saltapp.client.SaltClient` with a small hand-written
  fake (`tests/conftest.py`'s `FakeSaltClient`) and exercises the REAL
  `saltapp.crypto`/`saltapp.webhook`/`saltapp.cards` logic underneath --
  real PGP keys, a real HMAC signature computed and then re-verified,
  real card-block validation. Nobody has run any of these six components
  against a live Salt account yet. See `HANDOFF.md`'s UAT steps before
  calling any of them production-ready.
- **Ask Human's check and Read Updates' check are single-process,
  single-flow-run concerns.** If two flow runs for the same agent overlap
  in time (two concurrent Ask Human calls, or an Ask Human call racing a
  Read Updates call), they call `GET /api/v1/agent/updates` against the
  SAME shared cursor file (see "File-based cursor state" above) and could
  race writing it (last write wins, no locking) -- a message consumed by
  one could advance the cursor out from under a check the other has
  in flight. This has not come up in practice (a Langflow flow run is not
  typically fanned out concurrently against one agent), but if it becomes
  a real scenario, either give each call site its own cursor file (for
  example, suffixed by the card_id) or add simple file locking to
  `PersistentCursor`.
- **No `Data`-typed second output on Ask Human.** The brief allowed one
  (`Message` for the answer plus an optional structured `Data` output);
  this build keeps it to one output (`Message`) with the structured
  detail (who answered, the raw `action_id`) in `self.status` instead, to
  keep the component's surface as small as the task asked for. Revisit
  if a flow author needs the structured shape as a real wired output
  rather than a status-line string.
- **`event_types` on Salt Read Updates is a plain comma-separated string
  field, not a Langflow multi-select.** `lfx.io.MultiselectInput` exists
  and would give a nicer picker UI in the visual editor; a plain text
  field was used instead to match the parsing helper
  (`_salt_common.parse_options`) already written for Ask Human's
  `options` field, and because Salt Read Updates is meant to sit at the
  start of a scheduled/triggered flow (static per-flow config), not to be
  called as an Agent tool the way Ask Human is -- `event_types`
  deliberately has no `tool_mode` set.
- **`SaltInterestsComponent`'s `action` and `mode` fields are
  `lfx.io.DropdownInput`, not validated at the input level.** `required`
  on `mode` stays `False` even though it is effectively required when
  `action="set"`, because a Langflow input's `required` flag is static
  and cannot depend on another field's current value; `apply()` validates
  the combination itself and returns a `{"status": "error", ...}` `Data`
  result rather than raising, matching this package's existing
  no-exception-on-a-user-shaped-error style (see Salt Ask Human's
  no-options short circuit).
