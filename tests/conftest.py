# Test fixtures shared by every test module in this package.
#
# `FakeSaltClient` stands in for `saltapp.client.SaltClient` -- no httpx
# transport, no network, just the same method surface the components call,
# recording every call so a test can assert on it. `signed_update_row`
# builds a socket-mode "update" row with a REAL HMAC signature (the exact
# scheme `saltapp.webhook.verify_signature` checks), so tests exercise the
# actual signature-verification path in `_salt_common.check_for_event`
# rather than mocking it away -- matching saltapp-python's own testing
# philosophy (AGENTS.md: "None of them stub the thing they claim to test").
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

import pytest

from saltapp_langflow import _salt_common as sc

WEBHOOK_SECRET = "test-webhook-secret"


class FakeSaltClient:
    """A drop-in stand-in for `saltapp.client.SaltClient`. Every method a
    component might call is here; each records its call args in `.calls`
    and returns whatever `self.<method>_response` (or a per-call queue) is
    set to, so a test can script a sequence of poll rounds."""

    def __init__(self, host: str = "https://example.test") -> None:
        self.host = host
        self.closed = False
        self.calls: list[tuple[str, tuple, dict]] = []

        self.who_am_i_response: dict[str, Any] = {"agent_id": "agent-self", "webhook_secret": WEBHOOK_SECRET}
        self.chat_members_response: list[dict[str, Any]] = []
        self.get_chat_response: dict[str, Any] = {"session": {"encrypted": True, "users": []}, "messages": []}
        self.post_message_response: dict[str, Any] = {"id": "msg-1"}
        self.post_plain_message_response: dict[str, Any] = {"id": "msg-plain-1"}
        self.post_card_response: dict[str, Any] = {"id": "card-1"}
        self.request_payment_response: dict[str, Any] = {"id": "req-1"}
        self.get_chat_subscription_response: dict[str, Any] = {"chat_id": "chat-1", "mode": "addressed", "keywords": []}
        self.set_chat_subscription_response: dict[str, Any] = {"chat_id": "chat-1", "mode": "addressed", "keywords": []}
        self.clear_chat_subscription_response: dict[str, Any] = {"chat_id": "chat-1", "mode": "addressed", "keywords": []}
        # A list of "rounds": each call to get_agent_updates pops the next
        # round (or repeats the last one once the list is exhausted).
        self.update_rounds: list[dict[str, Any]] = [{"updates": [], "cursor": 0}]
        self._round_index = 0

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))

    def close(self) -> None:
        self.closed = True

    def who_am_i(self, api_key: str) -> dict[str, Any]:
        self._record("who_am_i", api_key)
        return self.who_am_i_response

    def get_chat_members(self, api_key: str, chat_id: str) -> list[dict[str, Any]]:
        self._record("get_chat_members", api_key, chat_id)
        return self.chat_members_response

    def get_chat(self, api_key: str, chat_id: str, *, last: Any = None) -> dict[str, Any]:
        self._record("get_chat", api_key, chat_id, last=last)
        return self.get_chat_response

    def post_message(
        self,
        api_key: str,
        chat_id: str,
        message: str,
        sender_message: str | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        self._record("post_message", api_key, chat_id, message, sender_message, **kwargs)
        return self.post_message_response

    def post_plain_message(self, api_key: str, chat_id: str, message: str, **kwargs: Any) -> dict[str, Any]:
        self._record("post_plain_message", api_key, chat_id, message, **kwargs)
        return self.post_plain_message_response

    def post_card(self, api_key: str, chat_id: str, blocks: list[dict[str, Any]], text: str) -> dict[str, Any]:
        self._record("post_card", api_key, chat_id, blocks, text)
        return self.post_card_response

    def request_payment(self, api_key: str, **kwargs: Any) -> dict[str, Any]:
        self._record("request_payment", api_key, **kwargs)
        return self.request_payment_response

    def get_chat_subscription(self, api_key: str, chat_id: str) -> dict[str, Any]:
        self._record("get_chat_subscription", api_key, chat_id)
        return self.get_chat_subscription_response

    def set_chat_subscription(
        self, api_key: str, chat_id: str, mode: str, *, keywords: list[str] | None = None
    ) -> dict[str, Any]:
        self._record("set_chat_subscription", api_key, chat_id, mode, keywords=keywords)
        return self.set_chat_subscription_response

    def clear_chat_subscription(self, api_key: str, chat_id: str) -> dict[str, Any]:
        self._record("clear_chat_subscription", api_key, chat_id)
        return self.clear_chat_subscription_response

    def get_agent_updates(self, api_key: str, *, after: int = 0, timeout: int = 2, limit: int = 100) -> dict[str, Any]:
        self._record("get_agent_updates", api_key, after=after, timeout=timeout, limit=limit)
        index = min(self._round_index, len(self.update_rounds) - 1)
        round_ = self.update_rounds[index]
        self._round_index += 1
        return round_


def sign_body(body: dict[str, Any], secret: str = WEBHOOK_SECRET, *, timestamp: int | None = None) -> tuple[dict, str]:
    """The exact `X-Salt-Signature: t=..,v1=..` scheme saltapp.webhook checks.
    Returns (headers, raw_json_string) -- the raw string is what a test
    should also put in the update row's `body`, so the bytes hashed for the
    signature match the bytes `_salt_common.check_for_event` re-hashes."""
    ts = timestamp if timestamp is not None else int(time.time())
    raw = json.dumps(body)
    signed_string = f"{ts}.".encode() + raw.encode("utf-8")
    digest = hmac.new(secret.encode("utf-8"), signed_string, hashlib.sha256).hexdigest()
    headers = {"X-Salt-Signature": f"t={ts},v1={digest}"}
    return headers, raw


def signed_update_row(
    row_id: int,
    body: dict[str, Any],
    *,
    secret: str = WEBHOOK_SECRET,
    delivery_id: str | None = None,
    agent_id: str = "agent-self",
) -> dict[str, Any]:
    """One `GET /api/v1/agent/updates` row, correctly signed."""
    headers, raw = sign_body(body, secret)
    headers["X-Salt-Agent-Id"] = agent_id
    if delivery_id is not None:
        headers["X-Salt-Delivery-Id"] = delivery_id
    return {
        "id": row_id,
        "headers": headers,
        "body": raw,
        "delivery_id": delivery_id,
        "created_at": "2026-09-18T00:00:00Z",
    }


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    """Every test gets its own poll-cursor directory and a clean
    who_am_i cache -- without this, PersistentCursor would read/write a
    real ~/.salt/agents/... directory, and a stale cached identity from an
    earlier test (same api_key) would leak into a later one."""
    monkeypatch.setenv("SALTAPP_LANGFLOW_STATE_DIR", str(tmp_path / "state"))
    sc.reset_whoami_cache()
    yield
    sc.reset_whoami_cache()
