# Shared helpers for every Salt (saltapp.ai) Langflow component in this
# package: building a client, resolving an agent's own identity, the
# encrypted-send helper (Salt Send Message), and the short-poll helper both
# Salt Ask Human and Salt Trigger/Listen use to wait for a verified event.
#
# Deliberately thin: every real Salt call goes straight through
# `saltapp.client.SaltClient` with plain strings pulled from a component's
# field values -- no `saltapp.identity.Identity` object, no
# `saltapp.integrations.*` (that layer is built for a persistent
# framework-agent process; a Langflow component runs once per flow
# invocation, so it does not fit). See AGENTS.md for the full rationale.
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Callable

from saltapp import cards, crypto
from saltapp.client import SaltClient
from saltapp.errors import SaltApiError
from saltapp.webhook import Event, WebhookVerificationError
from saltapp.webhook import handle as handle_webhook

SALT_HOST_DEFAULT = "https://saltapp.ai"

# Every component's optional private-key/passphrase fields carry this same
# plain-language note (rule: no "vault"/"key"/"encrypt" scare language,
# state plainly what needs a private key and what doesn't). None of the
# four components in this package ever decrypt anything: sending encrypts
# TO other people's public keys (no private key needed to do that), a
# button tap and every socket-mode event body arrive as plain structured
# JSON (never PGP ciphertext), and a payment request is a plain API record.
KEYLESS_INFO = (
    "Not needed by this component. Sending, posting and requesting only "
    "use recipients' public keys, fetched live from the chat; nothing here "
    "reads or decrypts message text."
)

# One poll round's short-poll timeout passed to the server (clamped there to
# 0..2s regardless of what is sent -- see SaltClient.get_agent_updates).
_POLL_TIMEOUT_SECONDS = 2
_POLL_SLEEP_SECONDS = 1.0

# Deliberately NOT `saltapp.socket.SOCKET_SIGNATURE_TOLERANCE_SECONDS`: that
# constant is still the pre-round-4 widened value (7 days + 1h) as of this
# writing (see saltapp-python's own pending fix). LANES.md's round-3/4
# socket contract, "fix A" (serve-time signing): salt-api now re-signs
# every outbox row FRESH, over the stored body, at the moment it's actually
# served by GET /api/v1/agent/updates -- never once at enqueue time. So a
# row that sat unpolled for the full 7-day retention window verifies with a
# signature timestamped as if written just now, and the STANDARD ~300s
# tolerance (matching the webhook path) is correct and sufficient here too.
# The wider tolerance is a real weakness now: it would accept a signature
# far older than any genuine serve-time one could be. Defined locally
# rather than imported so this package doesn't inherit that upstream bug.
POLL_SIGNATURE_TOLERANCE_SECONDS = 300

# who_am_i answers are cached per api-key for the life of this process --
# cheap to skip re-fetching on every component run, and safe to keep stale
# for a whole process lifetime since a webhook secret only changes on an
# explicit rotation (a rotated secret just means an old cached copy fails
# verification loudly, not silently).
_WHOAMI_CACHE: dict[str, dict[str, Any]] = {}


def reset_whoami_cache() -> None:
    """Test-only: clear the per-process who_am_i cache between test cases."""
    _WHOAMI_CACHE.clear()


def get_client(host: str) -> SaltClient:
    return SaltClient(host or SALT_HOST_DEFAULT)


def resolve_identity(client: SaltClient, api_key: str) -> dict[str, Any]:
    """`who_am_i`, cached per api-key within this process (see the module
    docstring above the cache dict for why caching is safe here)."""
    cached = _WHOAMI_CACHE.get(api_key)
    if cached is not None:
        return cached
    info = client.who_am_i(api_key)
    _WHOAMI_CACHE[api_key] = info
    return info


def parse_options(raw: str | None) -> list[str]:
    """A comma-separated field value -> a clean list of non-empty labels."""
    return [item.strip() for item in (raw or "").split(",") if item.strip()]


def send_encrypted_message(
    client: SaltClient,
    *,
    api_key: str,
    agent_id: str,
    chat_id: str,
    text: str,
    mentions: list[str] | None = None,
    quiet: bool = False,
) -> dict[str, Any]:
    """Encrypt `text` for every other member of `chat_id` and post it.

    Mirrors `SaltClient.send_message`'s own logic but takes plain strings
    instead of a `saltapp.identity.Identity` object (see the module
    docstring): fetch the chat's members, split them into "everyone but
    this agent" (the real recipients) and, if this agent's own public key
    is among the members, a self-copy so this agent's own history stays
    legible. Raises SaltApiError if nobody else in the chat has a public
    key on file yet.
    """
    members = client.get_chat_members(api_key, chat_id)
    self_id = str(agent_id or "").lower()
    recipient_keys: list[str] = []
    own_public_key: str | None = None
    for member in members:
        public_key = member.get("public_key")
        if not public_key:
            continue
        if str(member.get("id", "")).lower() == self_id:
            own_public_key = public_key
        else:
            recipient_keys.append(public_key)

    if not recipient_keys:
        raise SaltApiError(
            "POST",
            f"{client.host}/api/v1/messages",
            0,
            {"error": "no recipient public keys for this chat; not sending."},
        )

    encrypted = crypto.encrypt_for(text, recipient_keys)
    sender_copy = crypto.encrypt_for(text, [own_public_key]) if own_public_key else None
    return client.post_message(api_key, chat_id, encrypted, sender_copy, mentions=mentions, quiet=quiet)


def build_question_card(question: str, options: list[str]) -> list[dict[str, Any]]:
    """A one-section, one-actions-row card: the question, then one button
    per option, `action_id`s `opt_0`, `opt_1`, ... in the given order."""
    buttons = [cards.button(f"opt_{i}", label[: cards.MAX_LABEL]) for i, label in enumerate(options)]
    return cards.blocks(cards.section(text=question), cards.actions(buttons))


def _state_root() -> Path:
    override = os.environ.get("SALTAPP_LANGFLOW_STATE_DIR")
    return Path(override) if override else Path.home() / ".salt" / "agents"


def state_dir(agent_id: str, purpose: str) -> Path:
    """Where this package keeps its own small per-agent, per-purpose state
    (just a poll cursor so far) -- a subdirectory of the same `~/.salt/
    agents/<agent_id>/` tree `saltapp.socket`'s file-backed stores use, kept
    apart under `langflow/<purpose>/` so this package's cursor files never
    collide with a socket-mode `Agent`'s own cursor/dedupe files for the
    same agent. Override the root with SALTAPP_LANGFLOW_STATE_DIR (tests do
    this to avoid touching a real home directory)."""
    directory = _state_root() / (agent_id or "unknown") / "langflow" / purpose
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    return directory


class PersistentCursor:
    """A tiny file-backed high-water-mark: one integer, so a later flow run
    (Salt Ask Human's next call, or Salt Trigger/Listen's next poll) does
    not re-scan updates an earlier run already consumed. Best-effort by
    design -- a missing or corrupt file just starts over from 0 (re-scans
    instead of crashing); this is a performance optimization, never a
    correctness requirement, since every caller here also matches on the
    specific thing it is looking for (a card_id, a requested event type)."""

    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self) -> int:
        try:
            return int(json.loads(self._path.read_text())["after"])
        except (OSError, ValueError, KeyError, json.JSONDecodeError):
            return 0

    def set(self, value: int) -> None:
        try:
            self._path.write_text(json.dumps({"after": int(value)}))
            self._path.chmod(0o600)
        except OSError:
            pass  # best-effort; the next call just re-scans from its old position.


def _row_raw_bytes(update: dict[str, Any]) -> bytes:
    raw_body = update.get("body")
    if isinstance(raw_body, str):
        return raw_body.encode("utf-8")
    return json.dumps(raw_body or {}).encode("utf-8")


def poll_for_event(
    client: SaltClient,
    api_key: str,
    *,
    secret: str,
    cursor: PersistentCursor,
    wait_seconds: float,
    predicate: Callable[[Event], bool],
    sleep_seconds: float = _POLL_SLEEP_SECONDS,
    tolerance_seconds: int = POLL_SIGNATURE_TOLERANCE_SECONDS,
) -> Event | None:
    """Short-poll `GET /api/v1/agent/updates` (the same K2 socket-mode
    contract `saltapp.socket.SocketClient` uses) for up to `wait_seconds`,
    verifying each row's signature at the standard tolerance (see
    POLL_SIGNATURE_TOLERANCE_SECONDS's own comment for why this is NOT the
    wider one `saltapp.socket` still uses), and returns the first `Event`
    for which `predicate(event)` is true, or None if `wait_seconds` elapses
    first.

    The cursor advances past every row seen in every round -- including
    rows the predicate rejected and the one it matched -- so a later call
    never re-delivers what this call already returned or discarded.

    One deliberate simplification versus `saltapp.socket.SocketClient`:
    that class HALTS (never advancing its cursor) on a row that fails
    verification, because it is a long-running daemon that can retry
    indefinitely. This helper instead skips a bad row and keeps going --
    a bounded `wait_seconds` wait cannot afford to halt forever on one
    unverifiable row while a human or a flow is waiting on the other end.
    """
    deadline = time.monotonic() + max(0.0, wait_seconds)
    after = cursor.get()
    match: Event | None = None

    while True:
        try:
            response = client.get_agent_updates(api_key, after=after, timeout=_POLL_TIMEOUT_SECONDS, limit=100)
        except SaltApiError:
            raise  # a real API failure (bad api-key, unreachable host) -- surface it, don't swallow it as "no answer".
        except Exception:  # noqa: BLE001 -- a transport hiccup; retry within the remaining wait.
            if time.monotonic() >= deadline:
                return None
            time.sleep(sleep_seconds)
            continue

        updates = response.get("updates") or []
        server_cursor = response.get("cursor", after)

        for update in updates:
            row_id = update.get("id")
            headers = update.get("headers") or {}
            try:
                event = handle_webhook(
                    headers,
                    _row_raw_bytes(update),
                    secret=secret,
                    verify=True,
                    tolerance_seconds=tolerance_seconds,
                )
            except WebhookVerificationError:
                if row_id is not None:
                    after = row_id
                continue
            if update.get("delivery_id") is not None:
                event.delivery_id = str(update["delivery_id"])
            if update.get("created_at") is not None:
                event.created_at = str(update["created_at"])
            if row_id is not None:
                after = row_id
            if match is None and predicate(event):
                match = event

        cursor.set(server_cursor if server_cursor is not None else after)

        if match is not None:
            return match
        if time.monotonic() >= deadline:
            return None
        time.sleep(sleep_seconds)
