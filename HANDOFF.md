# HANDOFF.md

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
  regardless of what is sent"), 1s between rounds, a real cursor
  (`PersistentCursor`) persisted to disk across component runs. NOT fixed
  (out of this repo's scope): `saltapp.client.SaltClient.get_agent_updates`
  (in the separate `saltapp-python` package this depends on via git)
  always sends `after=<cursor>` literally, including `after=0` on a fresh
  cursor, instead of omitting it so salt-api's server-side ack applies.
  Flagged to the coordinator; not this repo's file to fix.
- No brand-icon fix here: Langflow components reference a bundled Lucide
  icon by name (`icon = "radio"`, `"send"`, etc.), not a custom SVG asset
  slot -- there's nothing hand-drawn to replace.
- 13 -> 17 tests passing.

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
