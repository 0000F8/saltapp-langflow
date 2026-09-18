# AGENTS.md

Design notes for whoever works on this repo next.

## What this is

Four Langflow custom components (`saltapp_langflow/`) exposing a slice of
[Salt](https://saltapp.ai)'s agent actions inside a Langflow flow, built
on the [`saltapp`](https://github.com/0000F8/saltapp-python) Python SDK:
`SaltSendMessageComponent`, `SaltAskHumanComponent`,
`SaltRequestPaymentComponent`, `SaltListenComponent`. A sibling Dify
plugin build covers a larger, 7-tool surface (`get_answer`,
`send_invoice`, `post_card`, `get_payment_status`, and so on) against the
same SDK -- this repo is deliberately smaller. Do not grow this repo's
tool list to match that one; if a real need shows up for one of those
actions here, that is a separate, deliberate decision, not a default.

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

## The ask/poll and listen-poll designs

Both **Salt Ask Human** and **Salt Trigger/Listen** need to wait for
something to happen on Salt and then return it -- but a Langflow
component's build method is a plain synchronous call with no persistent
event loop behind it (see above), so neither can use
`saltapp.agent.Agent.ask()`/`approve()` (built on an in-process
`_AskRegistry` a running `Agent`'s webhook/socket dispatch feeds) or
`saltapp.socket.SocketClient.run()` (a `while True` loop with adaptive
backoff, meant to run forever). Both are instead built as a **bounded
short-poll loop**, in `_salt_common.poll_for_event`:

1. Call `GET /api/v1/agent/updates?after=<cursor>` (the K2 socket-mode
   contract -- the same endpoint `saltapp.socket.SocketClient` polls).
2. For each row in the response, verify its `X-Salt-Signature` header at
   `saltapp.socket.SOCKET_SIGNATURE_TOLERANCE_SECONDS` (retention + 1h --
   an outbox row can sit unpolled for days, so the ordinary 300s webhook
   tolerance is too tight here), classify it into an `Event`, and check
   the caller's `predicate`.
3. Advance the cursor past every row seen this round (verified or not,
   matched or not) so a later call never re-processes what this call
   already consumed.
4. If nothing matched and the caller's `wait_seconds` budget has not
   elapsed, sleep briefly (`_POLL_SLEEP_SECONDS`, currently 1s) and go
   back to step 1. Otherwise return the match, or `None`.

Deliberate simplification versus `saltapp.socket.SocketClient`: that
class **halts** (never advancing its cursor) on a row that fails
signature verification, because it is a long-running daemon that can
retry the same row indefinitely on the next iteration. This helper
instead skips a bad row and keeps going -- a bounded `wait_seconds` wait
cannot afford to get stuck retrying one unverifiable row forever while a
human or a flow run is waiting on the other end. If Salt's outbox ever
hands back a row that fails verification, that row is simply treated as
"not the thing we were waiting for" and the poll continues.

Salt Ask Human's predicate matches a `card_interaction` event whose
`card_id` equals the card this call itself just posted, tapped by a
non-agent user. Salt Trigger/Listen's predicate matches any event whose
type is in the caller's optional `event_types` filter (or any classified
type at all, if the filter is blank) -- it has no "thing it posted" to
correlate against, since it is meant to observe whatever happens.

## File-based cursor state

`_salt_common.PersistentCursor` is a one-integer JSON file under
`~/.salt/agents/<agent_id>/langflow/<purpose>/cursor.json` (override root
via `SALTAPP_LANGFLOW_STATE_DIR`, which the test suite uses to avoid
touching a real home directory). `<purpose>` is `"ask_human"` or
`"listen"` so the two components never share (and stomp on) each other's
position in the same agent's update stream. This lives in the same
`~/.salt/agents/` tree `saltapp.socket`'s own file-backed cursor/dedupe
stores use, but under a `langflow/` subdirectory specifically so this
package's cursor files never collide with a socket-mode `Agent`'s own
files for the same agent id, if both happen to run against the same
account on the same machine.

This is a **performance optimization, not a correctness requirement**:
every poll loop still matches on something specific (a card_id, a
requested event type), so re-scanning old history would just waste time,
never produce a wrong answer. A missing or corrupt cursor file is treated
as "start from 0" and the loop just re-scans -- never a hard failure.

## The keyless boundary, and why even Ask Human needs no private key

Every component's `private_key`/`passphrase` fields are optional, and
nothing in this package actually uses them:

- Sending a message needs recipients' **public** keys only (fetched live
  from the chat's member list via `get_chat_members`) -- a sender never
  needs its own private key to encrypt *to* someone else.
- A card tap, a payment confirmation, a chat-opened notice, a hand-off
  notice -- every event `_salt_common.poll_for_event` can return -- arrive
  as **plain structured JSON**, never PGP ciphertext. Salt's own card
  protocol is deliberately unencrypted metadata (who tapped what, when);
  only message *bodies* are end-to-end encrypted. So even Salt Ask Human,
  whose whole job is "read the human's answer", never needs to decrypt
  anything -- there is nothing encrypted to decrypt in a button tap.
- A payment request is a plain API record on the TransferRequest rail; no
  PGP involved at either end.

If a future component in this package needs to read encrypted message
*text* (for example, a "read the last message in this chat" component),
that is the first place `private_key`/`passphrase` would actually get
used, via `saltapp.crypto.decrypt`. Keep the field present-but-unused
pattern until that day rather than removing the fields now -- removing
them would mean a different field layout across this package's own
components for no present benefit.

## Known limitations

- **No live end-to-end test against a running salt-api.** Every test in
  `tests/` mocks `saltapp.client.SaltClient` with a small hand-written
  fake (`tests/conftest.py`'s `FakeSaltClient`) and exercises the REAL
  `saltapp.crypto`/`saltapp.webhook`/`saltapp.cards` logic underneath --
  real PGP keys, a real HMAC signature computed and then re-verified,
  real card-block validation. Nobody has run any of these four components
  against a live Salt account yet. See `HANDOFF.md`'s UAT steps before
  calling any of them production-ready.
- **Ask Human's poll and Listen's poll are single-process, single-flow-run
  concerns.** If two flow runs for the same agent overlap in time (two
  concurrent Ask Human calls, or an Ask Human call racing a Listen call),
  they poll the same `GET /api/v1/agent/updates` stream independently and
  each advances its own cursor file: a message consumed by one does not
  disappear for the other polling on a different `<purpose>` cursor, but
  two *concurrent* pollers on the very same `<purpose>` cursor file could
  race writing it (last write wins, no locking). This has not come up in
  practice because Ask Human and Listen use different `<purpose>` values,
  but two concurrent Ask Human calls for the same agent would share one
  cursor file and could interleave. If that becomes a real scenario,
  either give each call site its own cursor file (for example, suffixed
  by the card_id) or add simple file locking to `PersistentCursor`.
- **No `Data`-typed second output on Ask Human.** The brief allowed one
  (`Message` for the answer plus an optional structured `Data` output);
  this build keeps it to one output (`Message`) with the structured
  detail (who answered, the raw `action_id`) in `self.status` instead, to
  keep the component's surface as small as the task asked for. Revisit
  if a flow author needs the structured shape as a real wired output
  rather than a status-line string.
- **`event_types` on Listen is a plain comma-separated string field, not
  a Langflow multi-select.** `lfx.io.MultiselectInput` exists and would
  give a nicer picker UI in the visual editor; a plain text field was
  used instead to match the parsing helper (`_salt_common.parse_options`)
  already written for Ask Human's `options` field, and because Listen is
  meant to sit at the start of a scheduled/triggered flow (static
  per-flow config), not to be called as an Agent tool the way Ask Human
  is -- `event_types` deliberately has no `tool_mode` set.
