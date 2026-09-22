# HANDOFF.md

## 2026-09-22 open rooms, no-polling rewrite (`lane/open-rooms`)

Branch `lane/open-rooms`, built against the sibling `saltapp-python`
checkout's own `lane/open-rooms` branch (saltapp 0.2.0, installed here via
`.venv/bin/pip install -e /path/to/saltapp-python` -- see `pyproject.toml`'s
updated dependency comment for why the git URL itself is untouched). Two
independent things landed in the same pass because the task bundled them:
saltapp 0.2.0's **open rooms**/**interests** support, and the owner's
separate, unrelated **"no polling, ever"** rule for this package's
event-checking components. Real end-to-end test count: **20 -> 32
passing** (`.venv/bin/python -m pytest -q` from the repo root):

```
32 passed, 22 warnings in 0.90s
```

(warnings are the same pre-existing third-party deprecation noise the
2026-09-22 alignment-pass entry below already documented -- pgpy/
cryptography/pydantic, nothing from this package's own code.)

### What changed

- **`pyproject.toml`**: documented saltapp's effective `>=0.2.0` floor in
  a comment (PEP 508 forbids combining a version specifier with the
  existing direct-URL dependency, so the git URL line itself is
  unchanged) and pointed local dev at the sibling editable checkout until
  saltapp-python's `lane/open-rooms` merges to its own `main`. Also
  refreshed the package `description` (it still said "poll for new Salt
  events").
- **`saltapp_langflow/_salt_common.py`**:
  - New `send_message()` -- reads a chat's `session.encrypted` flag and
    dispatches to `client.post_plain_message` (open room) or the
    (renamed-in-place) `send_encrypted_message()` (everything else).
    `send_encrypted_message()` gained an optional `members` parameter so
    `send_message()` can pass along the member list it already fetched
    via `get_chat` instead of paying for a second `get_chat_members`
    round trip -- it still defaults to fetching fresh, so any other
    caller is unaffected.
  - New `read_room()` -- thin wrapper over `client.get_chat`, `api_key`
    defaults to `""` so a falsy field value reaches `SaltClient` as "no
    api-key header at all" (the anonymous-open-room path).
  - `poll_for_event()` deleted; replaced by `check_for_event()` -- same
    per-row verify/advance-cursor logic, but ONE `get_agent_updates` call,
    no `wait_seconds`, no `time.sleep`, no loop, and it returns
    `(matches: list[Event], new_cursor: int)` instead of stopping at the
    first predicate hit.
  - `_POLL_SLEEP_SECONDS` deleted (nothing sleeps anymore); `import time`
    removed (nothing left that needs it).
- **`saltapp_langflow/send_message.py`**: calls `sc.send_message()`
  instead of `sc.send_encrypted_message()`; status line now says plain vs.
  encrypted and echoes `delivered_because` when the API returns it.
- **`saltapp_langflow/read_room.py`** (new): `SaltReadRoomComponent`.
  `api_key` is `required=False` and the ONLY field in this package that
  is genuinely optional at runtime, not just conventionally present; no
  `agent_id`/`private_key`/`passphrase` fields at all.
- **`saltapp_langflow/interests.py`** (new): `SaltInterestsComponent`,
  `action` (dropdown: set/get/clear) + `mode` (dropdown: addressed/
  keywords/all, required only when `action="set"`, validated in code
  since a Langflow input's `required` flag can't depend on another
  field's value) + `keywords`. Invalid input returns
  `{"status": "error", ...}`, never raises.
- **`saltapp_langflow/ask_human.py`**: `wait_seconds`/`MAX_WAIT_SECONDS`
  removed. New `card_id` input: blank posts a new card (as before) and
  the same one `check_for_event` call now also covers the instant-tap
  case; non-blank skips posting and re-checks that card. Either way it
  ALWAYS returns -- the answer, or `Message(text=f"pending:{card_id}")`.
- **`saltapp_langflow/read_updates.py`** (new, replaces `listen.py`,
  deleted): `SaltReadUpdatesComponent`. `wait_seconds`/
  `MAX_WAIT_SECONDS`/`DEFAULT_WAIT_SECONDS` removed. One
  `check_for_event` call, returns EVERY matching event from that one
  response page (`{"status": "events", "events": [...]}`), not just the
  first.
- **`saltapp_langflow/__init__.py`**: `SaltListenComponent`/`listen`
  dropped; `SaltReadUpdatesComponent`/`read_updates`,
  `SaltReadRoomComponent`/`read_room`, `SaltInterestsComponent`/
  `interests` added.
- **Tests**: `tests/conftest.py`'s `FakeSaltClient` gained `get_chat`,
  `post_plain_message`, `get_chat_subscription`/`set_chat_subscription`/
  `clear_chat_subscription`, all following the existing `_record`/
  `..._response` attribute pattern. The `_no_real_sleep` fixture was
  deleted (grepped first -- nothing calls `time.sleep` anymore).
  `tests/test_listen.py` deleted; `tests/test_read_updates.py`,
  `tests/test_read_room.py`, `tests/test_interests.py` added.
  `tests/test_ask_human.py` rewritten for the card_id/one-shot semantics,
  asserting exact `get_agent_updates` call counts (proving there is no
  loop, not just checking outcomes) alongside the answer/pending
  outcomes. `tests/test_send_message.py` gained the open-room case (with
  a `crypto.encrypt_for` spy proving the PGP path is untouched) and a
  case for the `session["users"]`-missing fallback; its existing
  encrypted-room test now sources members from `get_chat_response`'s
  `session.users` instead of the old standalone
  `chat_members_response` (both fields still exist on the fake; the
  fallback test is what actually exercises the old one now).
  `tests/test_shared_cursor.py`/`tests/test_signature_tolerance.py`
  updated for the `check_for_event` rename and its new
  `(matches, cursor)` return shape; the behavior they pin (cursor
  advance, signature tolerance) is unchanged.
- **Docs**: `README.md` and `AGENTS.md` rewritten in the affected
  sections (component list, the ask/check design, keyless boundary,
  file-based cursor state, known limitations) rather than appended to --
  per the task's instruction for AGENTS.md, and because leaving the old
  poll-loop prose next to the new one-shot design would have been
  actively misleading. Two new README sections, "No polling, ever" and
  "Push vs. on-demand", name `n8n-nodes-saltapp`'s `Salt Trigger` node
  and `saltapp.agent.Agent`/`create_asgi_app` as the concrete
  push-pairing options and are explicit that this package cannot host a
  webhook receiver itself.

### Deviations from the spec, and why

- **`check_for_event` no longer catches or retries a transport error from
  `get_agent_updates`.** The old `poll_for_event` caught a
  non-`SaltApiError` exception (a network hiccup) and retried it within
  the remaining `wait_seconds` budget, re-raising only a real
  `SaltApiError` immediately. With the loop gone there is no "remaining
  budget" to retry within, so `check_for_event` now makes its one call
  with no try/except at all -- ANY exception (`SaltApiError` or a bare
  transport error) propagates straight to the caller. This was not
  spelled out explicitly in the task brief, which only said "does exactly
  ONE `client.get_agent_updates(...)` call" with the existing verify-each-
  row/advance-cursor reasoning kept; letting every exception surface
  (never silently swallowing a hiccup into a false "no events") seemed
  the more defensible reading of "no loop, no retry" than inventing a new
  one-shot suppression rule with no round to retry in.
- **Kept a couple of small, cheap extra tests beyond the letter of the
  spec**: a fallback test for `send_message()`'s `session["users"]`-
  missing path (the real SDK's response always has that key today, so
  this path is otherwise dead code with no coverage), and a `mode`
  DropdownInput non-"keywords" case for Salt Interests asserting
  `keywords=None` reaches the API. Both are single test functions
  following the existing file's own pattern; neither broadens the
  production code.
- **`ask_human.py`'s re-check path still computes `label_by_action` from
  whatever `self.options` currently holds**, even on a re-check call
  (rather than only on a fresh post). The spec didn't say either way. If
  a caller re-supplies the same `options` on the re-check call (an Agent
  tool call can do this naturally), the returned answer is still the
  human-readable label instead of a raw `action_id`; if not, it falls
  back to the raw `action_id`, exactly as an empty-mapping re-check would
  have either way. This costs nothing (no extra API call) and never
  fails, so it was kept rather than special-cased away.
- **`AGENTS.md`'s "Known limitations" section had a pre-existing stale
  claim** ("Ask Human and Listen use different `<purpose>` values") left
  over from BEFORE that same file's own earlier 2026-09-22 entry (below)
  actually unified them onto `SHARED_POLL_PURPOSE`. Fixed while rewriting
  the surrounding paragraph for the Read Updates rename, since leaving a
  now-doubly-wrong sentence next to a passage this pass was already
  rewriting would have been worse than the small scope tick.

### What was NOT done / out of scope, per the task

- No live end-to-end run against a real `salt-api` with the new open-room
  endpoints -- same limitation as every earlier pass (`FakeSaltClient`
  only, no `httpx`), see "Known limitations" in AGENTS.md and the UAT
  steps below.
- No attempt at a persistent webhook receiver inside this package --
  explicitly out of scope per the task brief and per this repo's own
  established "components run once per invocation" architecture; the
  README's new "Push vs. on-demand" section documents the pairing instead.
- Not pushed, no PR opened, no other repo touched, per the task's
  instructions.

## 2026-09-22 alignment pass (round-4 socket contract)

- **Fixed a real round-3/4 contract violation**: `_salt_common.py` used to
  import and default to `saltapp.socket.SOCKET_SIGNATURE_TOLERANCE_SECONDS`
  (still the pre-round-4 widened value, 7 days + 1h, as of this pass). Per
  LANES.md's "fix A" (serve-time signing), salt-api now re-signs every
  outbox row fresh at the moment it's actually served, so a row that sat
  unpolled for the full 7-day retention window verifies with a signature
  timestamped as if written just now -- the standard ~300s tolerance is
  correct and sufficient, and the wide one is a real weakness (accepts a
  signature far older than any genuine serve-time one could be). Replaced
  with a local `POLL_SIGNATURE_TOLERANCE_SECONDS = 300`, defined rather
  than imported so this package doesn't inherit the upstream bug; a caller
  can still pass `tolerance_seconds` explicitly to override it. 4 new
  tests in `tests/test_signature_tolerance.py`, including one that proves
  a day-old-signed row (which the OLD default would have accepted) is now
  correctly rejected.
- **This package's own poll loop (`poll_for_event`) was already correctly
  aligned otherwise**: 2s round timeout ("clamped there to 0..2s
  regardless of what is sent"), 1s between rounds (kept fixed, not made
  adaptive -- team-lead's ruling: Ask Human/Listen are bounded ≤50-55s
  waits, a different shape from a long-lived listener, so backing off
  toward 5s would just eat into an already-short budget), a real cursor
  (`PersistentCursor`) persisted to disk across component runs.
  ~~NOT fixed... `SaltClient.get_agent_updates` always sends `after=0`...~~
  **Corrected by the coordinator after a live production check**: `after=0`
  and omitting the param resolve identically server-side
  (`resolved = max(agent_updates_acked_id, after.clamp(0, newest))`), so
  `saltapp.client.SaltClient.get_agent_updates`'s current behavior needed
  no change here. What actually matters, confirmed against production: the
  ack only advances on an EXPLICIT, monotonically increasing `after` --
  this package's cursor already does that correctly (see the new shared-
  cursor fix below, and `tests/test_shared_cursor.py`'s "a second poll
  does not redeliver processed rows" test).
- **Fixed a real bug this same production check surfaced: Ask Human and
  Listen used to poll through SEPARATE local cursor files
  (`purpose="ask_human"` vs `"listen"`), implying two independent
  positions that don't actually exist server-side** -- salt-api keeps
  exactly ONE ack per agent, not one per caller/purpose, so one
  component's poll could silently advance the real (shared) ack past rows
  the other component's own, staler local file still expected to see --
  those rows are then gone for good (the ack never rewinds). Both
  components now share one cursor file (`sc.SHARED_POLL_PURPOSE`).
  This does NOT make it safe to run them (or two Ask Human calls)
  concurrently against the same agent_id -- there is still only one true
  ack, sharing the file just stops this package's own bookkeeping from
  lying about it. Documented prominently in AGENTS.md and README.md; no
  lock exists or was added (a Langflow component has no natural place to
  hold one across concurrent flow executions -- serialize at the
  flow-orchestration level if you need concurrent polling against one
  agent). 3 new tests in `tests/test_shared_cursor.py`.
- No brand-icon fix here: Langflow components reference a bundled Lucide
  icon by name (`icon = "radio"`, `"send"`, etc.), not a custom SVG asset
  slot -- there's nothing hand-drawn to replace.
- 13 -> 20 tests passing.

---

## What changed

Brand-new repo. Built from scratch:

```
saltapp-langflow/
  saltapp_langflow/
    __init__.py            # re-exports the 4 component classes (Langflow's documented __init__.py pattern for a category folder)
    _salt_common.py         # SaltClient construction/caching, send_encrypted_message, build_question_card, PersistentCursor, poll_for_event
    send_message.py         # SaltSendMessageComponent
    ask_human.py             # SaltAskHumanComponent
    request_payment.py       # SaltRequestPaymentComponent
    listen.py                 # SaltListenComponent
  tests/
    __init__.py
    conftest.py             # FakeSaltClient, real-HMAC signed_update_row(), state/cache isolation fixtures
    test_send_message.py
    test_ask_human.py
    test_request_payment.py
    test_listen.py
  pyproject.toml
  README.md
  AGENTS.md
  HANDOFF.md
  .gitignore
```

## How to test it

```bash
cd saltapp-langflow
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"     # installs lfx + pytest, and saltapp from git+https://github.com/0000F8/saltapp-python@main
pytest -q
```

Real output from this build (macOS, Python 3.12.3, `lfx==1.12.2`,
`saltapp==0.1.1`):

```
13 passed, 16 warnings in 0.56s
```

The 16 warnings are all third-party deprecation noise (`pgpy`'s `imghdr`
use under Python 3.13's removal notice, a couple of `pydantic`
deprecations inside `lfx`/`pgpy` itself, and `cryptography` flagging a few
legacy ciphers `pgpy` still references) -- nothing from this package's own
code, and nothing that fails a test.

**What each test actually proves** (per the task's instruction not to
round up a weak test to "done"):

- `test_send_message.py` -- generates two REAL PGP keypairs
  (`saltapp.crypto.generate_keypair`), runs the component's `send()`
  against a faked `SaltClient`, then **decrypts** the ciphertext the
  component produced with each recipient's real private key and asserts
  the plaintext round-trips exactly. This is not a mocked-away assertion
  that `encrypt_for` was *called*; it is a check that what got sent is
  actually decryptable by the right people. Also covers the
  no-recipients-have-a-key error path.
- `test_ask_human.py` -- builds a real signed update row (real HMAC over
  the real JSON bytes, using `saltapp.webhook`'s own scheme) representing
  a card tap, and asserts the component's poll loop verifies it and
  returns the right option label. Separately covers: timeout with no
  taps at all, an agent's own tap being ignored (must not resolve a human
  ask), the 50s hard cap on `wait_seconds`, and the no-options short
  circuit that never calls the API.
- `test_request_payment.py` -- asserts the exact call shape reaching
  `SaltClient.request_payment`, including that `amount` is always sent as
  a string even when the field holds something that looks like a bare
  integer.
- `test_listen.py` -- covers an empty poll (`no_new_events`), filtering by
  `event_types` when two different event types land in the very same
  poll round, the 55s hard cap on `wait_seconds`, and -- the one that
  actually matters for correctness -- that a **second** component
  instance (simulating a second flow run) picks up the persisted cursor
  file from the first run and polls `after=<that cursor>` instead of
  `after=0`.

None of these stub away `saltapp.crypto` or `saltapp.webhook`; only
`saltapp.client.SaltClient` (the actual network boundary) is faked, via
`tests/conftest.py`'s `FakeSaltClient`, which never touches `httpx`.

**A note on why the first run of this suite was NOT 0.56s**: the first
draft of `test_ask_human_wait_seconds_is_capped_at_fifty` (and,
separately, the analogous `test_listen_wait_seconds_is_capped`) forgot to
fake the monotonic clock the poll loop checks against its deadline, so
that one test really slept out its full 50/55-second wait before the
fix. Caught by `pytest --durations=10` showing a single 50.00s test
before the fix; both now fake `time.monotonic` to jump forward instead of
sleeping, and the full suite runs in well under a second. Flagging this
plainly rather than quietly rounding it off, per the task's evidence
rules.

## `lfx`/`langflow` install status

`pip install lfx` (into a fresh venv, no `langflow` package involved)
succeeded cleanly -- about 2.5 minutes, ~140 packages, no build failures,
no missing wheels for this Python 3.12.3/macOS arm64 combination. Every
import the task's own component-API sketch names
(`lfx.custom.custom_component.component.Component`, `lfx.io.*`,
`lfx.schema.message.Message`, `lfx.schema.data.Data`) worked immediately.
`langflow` itself (the full app+DB stack) was never installed or needed
-- `lfx` alone was enough to write, instantiate, and run every component
class directly in Python, and is what this repo's own test suite installs
via the `dev` extra. `langflow`'s own `Webhook` component
(`lfx.components.input_output.webhook.WebhookComponent`) was read
directly from the installed package to confirm the "why not the built-in
Webhook" finding in README.md/AGENTS.md -- its `build_data` method really
does read only `self.data`, nothing header-shaped.

One real gotcha, documented in code comments and worth flagging here too:
a `Component` subclass's `__init__` calls `inspect.getsource()` on its
own module, so it must be defined in a real importable file -- instantiating
one from a `python -c "..."` string fails with `Could not find source code
for <Class>`. Every component in this package already lives in a real
file, so this only matters if someone tries to smoke-test a snippet
inline later.

## Trigger/Listen: built, not skipped

Built, as the poll-based design the task brief already specified.
Independently re-verified the premise before implementing it (read the
actual installed `lfx.components.input_output.webhook.WebhookComponent`
source, not just the docs): `build_data` really does build its output
from `self.data` alone, with zero header access, confirming Salt's
`X-Salt-Signature` header is structurally invisible to it. No new
information surfaced that changes the brief's conclusion, so the
recommended design was implemented as given: a bounded short-poll over
`GET /api/v1/agent/updates`, verifying each row itself, with a
file-persisted cursor so repeated flow runs do not re-deliver the same
event. See AGENTS.md's "The ask/poll and listen-poll designs" section for
the full write-up, and README.md's "Why not the built-in Webhook
component" for the user-facing version of the same finding.

## Getting this listed / distributed

Researched directly against `langflow-ai/langflow`'s own contributing
docs (`CONTRIBUTING.md` -> `docs/docs/Contributing/contributing-components.mdx`,
fetched from the repo, 2026-09-18) rather than guessing:

- There is **no separate "Langflow Store" or marketplace** distinct from
  the main repo. The only path to an "officially listed" component is a
  pull request against `langflow-ai/langflow` itself, adding the
  component under `src/lfx/src/lfx/components/<category>/`, plus any new
  dependency in that repo's own `pyproject.toml`, an entry in that
  category's `__init__.py`, a docs page under
  `docs/docs/Components/`, and tests written against that repo's own
  `ComponentTestBase` test-base classes -- reviewed and merged by the
  Langflow team.
- Short of that, the mechanism this README documents
  (`LANGFLOW_COMPONENTS_PATH` pointing at a components folder on disk) is
  the only other distribution path Langflow itself supports: an operator
  (or this package's own README) tells people to point the env var at a
  pip-installed or git-cloned copy of this repo.
- Per the task's explicit instructions, **no PR was opened, no repo was
  created on GitHub, and nothing was pushed anywhere.** This section is
  research for whoever decides to pursue listing later, not a claim that
  it happened.

## One-line "what's new" candidate

> Salt agents can now run inside a Langflow flow: send a message, ask a
> human a question and wait for their answer, or request a payment,
> using drop-in Langflow components.

(Written for a public-facing changelog per the workspace's own
"What's new is for users only" convention -- no internal/infra detail,
positive framing, no "fixed"/"bug" language. This repo has no version
history yet to attach it to; it is a candidate line for whoever ships
the first release that bundles it.)

## UAT steps against a real saltapp.ai account

None of this has been run against a live `salt-api` yet (see AGENTS.md's
"Known limitations" -- every test here mocks the network boundary). To
actually verify it:

1. Register two Salt accounts for this: a human account (or reuse an
   existing one) and an agent, both named `SALT-...`/`salt-...@example.test`
   style per the workspace's test-account convention. Register the agent
   via the Salt web app or `saltapp.client.SaltClient.create_agent`,
   capture its `id` (-> `agent_id`) and `api_key`.
2. Open a 1:1 chat between the human account and the agent on
   `saltapp.ai` (or your target Salt deployment) so there is a real
   `chat_id` and the human has a public key on file.
3. `export LANGFLOW_COMPONENTS_PATH=/path/to/saltapp-langflow` and
   `langflow run` (needs a real `langflow` install, not just `lfx` --
   `pip install langflow` in a throwaway venv for this step).
4. Build a tiny flow: a Salt Send Message node, fill in `host` (your
   deployment), `agent_id`, `api_key`, `chat_id`, and `text`, then run
   it. Confirm the message actually shows up in the chat on the real
   Salt app/web client, decrypted correctly for the human.
5. Add a Salt Ask Human node with the same credentials and `chat_id`,
   `question="Ping?"`, `options="Pong,Nope"`. Run it, then physically tap
   a button in the Salt app within 50 seconds. Confirm the flow's output
   shows the tapped label. Run it again and let it time out on purpose;
   confirm it returns a plain "No answer within 50s." result rather than
   an error.
6. Add a Salt Request Payment node with a real `wallet_id` the agent
   owns, a small test `amount`, and the human's user id as `receiver_id`.
   Run it and confirm a real payment-request bubble appears in the chat.
7. Add a Salt Trigger/Listen node with the same credentials. Send a
   message from the human side, then run the Listen node and confirm it
   returns that `message` event. Run it again immediately with nothing
   new having happened and confirm it returns `no_new_events` promptly
   rather than waiting out the full `wait_seconds`.
8. Wire Salt Ask Human into an Agent component's Tools input (as the
   README's worked example describes) and confirm the LLM can actually
   invoke it as a tool call, not just as a standalone node.

## Left undone / blocked

- No live end-to-end run against a real `salt-api` (see UAT steps above
  -- this needs a person with a real Salt account and a running Langflow
  instance, neither of which this task's environment had).
- No PR to `langflow-ai/langflow`, no GitHub repo, nothing pushed or
  published anywhere, per the task's explicit instructions.
- `saltapp` is still pre-PyPI, so this package's own dependency on it is
  a `git+https://` pin (`@main`, i.e. floating, not a pinned commit) --
  flagged in both `pyproject.toml`'s comment and the README. Once
  `saltapp` ships on PyPI, swap that one line for a normal version pin.
- The two known limitations named in AGENTS.md (no live e2e test; two
  concurrent pollers on the very same cursor file could race) are
  design-documented, not fixed -- neither came up as a real problem in
  this build, and fixing a race that has not been observed felt like
  scope creep past what the task asked for.
