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

## The ask/check design (2026-09-22 no-polling rewrite; Ask Human moved off the shared outbox 2026-09-26)

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

Two independent checks live in this package now, reading two different
endpoints, for two different reasons:

- **Salt Read Updates** still uses `_salt_common.check_for_event`
  (unchanged by the 2026-09-26 pass), which reads this agent's shared
  socket-mode outbox (`GET /api/v1/agent/updates`) -- see below.
- **Salt Ask Human** (rewritten 2026-09-26) reads the ONE card it itself
  posted (`GET /api/v1/cards/:id`, via `_salt_common.get_card`) instead.
  See "Salt Ask Human reads its own card, not the shared outbox" further
  down for why this changed and how it works now.

`_salt_common.check_for_event` is Salt Read Updates' one check:

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
which case it skips posting -- a re-check call) and then makes exactly
one check for a tap. Either way it ALWAYS returns: the answer if the
one-shot check found it (covers the instant-tap case), or a pending
result (`Message(text=f"pending:{card_id}")`) otherwise. There is no more
`wait_seconds` -- "wait for the answer" is now the CALLER's job:
re-invoke this same component with `card_id` set to what the pending
result returned, whenever it next wants to check. An Agent-driven flow
does this naturally (the LLM sees `"pending:<id>"`, and can be prompted
to call the tool again later with that id); a scheduled/cron flow does it
by construction (its next scheduled run passes the same `card_id`). What
that one check actually reads changed on 2026-09-26 -- see the next
section.

**Salt Read Updates** (was Salt Trigger/Listen) runs exactly one
`check_for_event` with a predicate matching the caller's optional
`event_types` filter (or any classified type at all, if blank) and
returns every matching event from that one response page as a list --
also unlike the old design, which returned at most one event and made
the caller re-run the whole component just to see if there was a second
one already sitting in the same page.

## Salt Ask Human reads its own card, not the shared outbox (2026-09-26)

Until 2026-09-26, Salt Ask Human's one check was ALSO a `check_for_event`
call against the shared socket-mode outbox, sharing its cursor file with
Salt Read Updates (see the git history of this section, and
`test_shared_cursor.py`, now deleted, which used to document exactly this
sharing). That outbox keeps exactly ONE forward-only `agent_updates_
acked_id` per agent, server-side, not one per caller or purpose: a check
with `after=X` moves the server's stored ack to `max(current_ack, X)`
regardless of who sent it, and the server then serves everyone from that
resolved position. Two independent problems fell out of this for Ask
Human specifically, confirmed against the same production facts salt-mcp
and saltapp-agentkit's own 2026-09-26 fixes were built on
(`design-fleet/runs/2026-09-17-distribution/FOLLOWUPS.md`):

1. **Two concurrent Ask Human calls against the same agent could consume
   each other's answers.** A Langflow flow can genuinely run more than
   one Agent-driven tool call concurrently (parallel tool calls in one
   LLM turn, or two separate flow runs); each Ask Human call posts its
   own distinct card, but both were reading the SAME shared cursor to
   check for a tap. Whichever checked first could advance the ack past a
   tap meant for the OTHER card, and that tap was then gone for good --
   not a performance problem, a genuine missed answer.
2. **An Ask Human check running beside a Salt Read Updates check on the
   same agent had the identical problem in the other direction.**

The fix: Salt Ask Human now reads the TAPPED CARD's own interaction log
instead (`GET /api/v1/cards/:id`, `_salt_common.get_card`, owner-only,
`interactions` newest-first, capped at 50 server-side). This is scoped to
one card and idempotent -- reading it never advances any shared position,
because there isn't one; any number of concurrent Ask Human calls, for
this agent or any other, can each resolve their own ask independently by
reading their own card. This mirrors salt-mcp's `pollForCardInteraction`/
`getCard` and saltapp-agentkit's Python `_get_card`/`poll_for_answer` --
see HANDOFF.md's 2026-09-26 entry for the full cross-reference.

Three consequences worth knowing if you touch `ask_human.py`:

- **No signature to verify.** The outbox's rows are signed deliveries
  (`X-Salt-Signature`, verified by `check_for_event` via
  `saltapp.webhook.handle`) because a webhook/socket delivery is
  push-shaped and needs to prove it really came from Salt. A card read is
  an ordinary authenticated REST call (api-key header, same trust model
  as `get_chat`/`post_card`), so there is nothing to verify here at all --
  `agent_id`/`resolve_identity`/`webhook_secret` are gone from this
  component entirely, along with the "no webhook secret" early return.
- **No account_type on a card interaction.** `CardInteraction#
  as_json_for_owner` (salt-api) is `{id, user_id, action_id, value,
  created_at}` -- unlike the old outbox event body, it never carries a
  full `user` object, so there is no `account_type` or `username` to read
  off the tap directly. Distinguishing a human's tap from another agent's
  (the one behavior this component has always guaranteed) now means
  fetching the chat's member list (`client.get_chat_members`) and
  cross-referencing the tapper's `user_id` against it -- fetched fresh on
  every check, and ONLY when there is at least one interaction to
  classify (skipped entirely on the common "nothing tapped yet" case, to
  avoid paying for it up front). A tapper no longer in the chat's member
  list is treated the same as an agent's tap: it doesn't resolve the ask
  (a deliberate simplification -- the old design would have kept trusting
  a snapshot of the tapper's account_type from the moment they tapped,
  which this rewrite has no equivalent of).
- **No `card_id` cross-check needed in the matching predicate.** The old
  design read a shared stream of every event this agent received and had
  to filter down to `card_interaction` rows whose OWN `card_id` matched
  the one being asked about. Reading `GET /api/v1/cards/:id` for a
  specific card already scopes every row in the response to that one
  card, so there is nothing left to filter on that axis.

**A 429 from the card read degrades to a pending result with a wait
hint**, rather than raising -- this is the one status this single-shot
check treats as "try again" rather than "something is wrong", since the
natural response (re-invoke with the same `card_id` shortly) is exactly
what a genuine "no tap yet" result already asks the caller to do. Every
other status still raises normally, same as before.

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

## File-based cursor state (now Salt Read Updates only)

`_salt_common.PersistentCursor` is a one-integer JSON file under
`~/.salt/agents/<agent_id>/langflow/<purpose>/cursor.json` (override root
via `SALTAPP_LANGFLOW_STATE_DIR`, which the test suite uses to avoid
touching a real home directory). This lives in the same `~/.salt/agents/`
tree `saltapp.socket`'s own file-backed cursor/dedupe stores use, but
under a `langflow/` subdirectory specifically so this package's cursor
files never collide with a socket-mode `Agent`'s own files for the same
agent id, if both happen to run against the same account on the same
machine.

**`<purpose>` is `sc.READ_UPDATES_POLL_PURPOSE` ("read_updates"), and Salt
Read Updates is the only component in this package that constructs a
`PersistentCursor` at all (as of 2026-09-26).** Until that date, this
purpose was `sc.SHARED_POLL_PURPOSE` ("poll") and was shared with Salt
Ask Human, on the reasoning that salt-api keeps exactly ONE ack per agent
(`users.agent_updates_acked_id`), not one per local purpose string, so
two separate local cursor files would have implied two independent
positions that don't actually exist server-side. Salt Ask Human no longer
polls this outbox at all (see "Salt Ask Human reads its own card, not the
shared outbox" above), so there is only one caller of this cursor left,
and nothing left to share it with. `SHARED_POLL_PURPOSE` is kept as an
alias of the new constant for one release, in case anything outside this
package's own components imported the old name directly.

**This does NOT make it safe to run two Salt Read Updates checks (or a
Read Updates check beside any other consumer of this agent's outbox, such
as a socket-mode `Agent`) concurrently against the same agent_id.** The
underlying constraint is "one caller at a time per agent" -- there is no
lock here (a Langflow component has no natural place to hold one across
concurrent flow executions); if your deployment genuinely needs
concurrent on-demand checks against one Salt agent's outbox, serialize it
at the flow-orchestration level, not inside this package. (Salt Ask
Human, since 2026-09-26, has no such constraint at all -- see above.)

This is otherwise a **performance optimization, not a correctness
requirement** in the ordinary (single-caller) case: every `check_for_event`
call matches on something specific (a requested event type), so
re-scanning old history would just waste time, never produce a wrong
answer. A missing or corrupt cursor file is treated as "start from 0" and
the next check just re-scans -- never a hard failure.

## The keyless boundary, and why even Ask Human needs no private key

Every component's `private_key`/`passphrase` fields are optional, and
nothing in this package actually uses them:

- Sending a message needs recipients' **public** keys only (fetched live
  from the chat's member list -- see `_salt_common.send_message`) -- a
  sender never needs its own private key to encrypt *to* someone else.
- A card tap (whether read from a card's own interaction log, as Salt Ask
  Human does since 2026-09-26, or from an event `_salt_common.
  check_for_event` returns), a payment confirmation, a chat-opened
  notice, a hand-off notice -- all arrive as **plain structured JSON**,
  never PGP ciphertext. Salt's own card protocol is deliberately
  unencrypted metadata (who tapped what, when); only message *bodies* are
  end-to-end encrypted. So even Salt Ask Human, whose whole job is "read
  the human's answer", never needs to decrypt anything -- there is
  nothing encrypted to decrypt in a button tap.
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
- **Read Updates' check is still a single-process, single-flow-run
  concern (Ask Human's no longer is, as of 2026-09-26).** If two flow
  runs for the same agent overlap in time and both call Salt Read
  Updates, they read/write the SAME local cursor file (see "File-based
  cursor state" above) and could race writing it (last write wins, no
  locking) -- a row consumed by one could advance the cursor out from
  under a check the other has in flight, and both still share salt-api's
  one true per-agent ack regardless of the local file. This has not come
  up in practice (a Langflow flow run is not typically fanned out
  concurrently against one agent's Read Updates), but if it becomes a
  real scenario, either give each call site its own cursor file or add
  simple file locking to `PersistentCursor`. Salt Ask Human has no
  equivalent concern any more: it reads its own card by id, which is not
  shared state between calls at all.
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
