"""_salt_common.poll_for_event's signature-verification tolerance.

Round-3/4 socket contract (LANES.md K2, "fix A" -- serve-time signing):
salt-api re-signs every outbox row fresh at the moment it's actually served,
so the standard ~300s tolerance is correct even for a row that sat unpolled
for days -- see POLL_SIGNATURE_TOLERANCE_SECONDS's own comment in
_salt_common.py for why this is deliberately NOT saltapp.socket's own
(still pre-round-4) constant.
"""
from __future__ import annotations

import time

from saltapp_langflow import _salt_common as sc

from .conftest import FakeSaltClient, WEBHOOK_SECRET, sign_body


def test_poll_signature_tolerance_default_is_the_standard_300s_not_the_wide_socket_value():
    assert sc.POLL_SIGNATURE_TOLERANCE_SECONDS == 300


def test_poll_for_event_accepts_a_row_signed_moments_ago(tmp_path):
    client = FakeSaltClient()
    body = {"message": {"id": "msg-1"}, "chat_id": "chat-1"}
    headers, raw = sign_body(body, timestamp=int(time.time()))
    headers["X-Salt-Agent-Id"] = "agent-self"
    row = {"id": 1, "headers": headers, "body": raw, "delivery_id": "d-1", "created_at": "now"}
    client.update_rounds = [{"updates": [row], "cursor": 1}]

    cursor = sc.PersistentCursor(tmp_path / "cursor.json")
    event = sc.poll_for_event(
        client, "sk-test", secret=WEBHOOK_SECRET, cursor=cursor, wait_seconds=0, predicate=lambda e: True,
    )

    assert event is not None
    assert event.body == body


def test_poll_for_event_rejects_a_row_signed_a_day_ago_now_that_the_tolerance_is_standard(tmp_path):
    """This exact row would have PASSED under the old, still-wide
    saltapp.socket.SOCKET_SIGNATURE_TOLERANCE_SECONDS (7 days + 1h) -- this
    test is what proves this package no longer uses that value."""
    client = FakeSaltClient()
    body = {"message": {"id": "msg-1"}, "chat_id": "chat-1"}
    one_day_ago = int(time.time()) - (24 * 60 * 60)
    headers, raw = sign_body(body, timestamp=one_day_ago)
    headers["X-Salt-Agent-Id"] = "agent-self"
    row = {"id": 1, "headers": headers, "body": raw, "delivery_id": "d-1", "created_at": "a day ago"}
    client.update_rounds = [{"updates": [row], "cursor": 1}]

    cursor = sc.PersistentCursor(tmp_path / "cursor.json")
    event = sc.poll_for_event(
        client, "sk-test", secret=WEBHOOK_SECRET, cursor=cursor, wait_seconds=0, predicate=lambda e: True,
    )

    assert event is None  # rejected as unverifiable at the standard tolerance, not matched
    assert cursor.get() == 1  # still advances past the bad row -- see poll_for_event's own docstring


def test_poll_for_event_honors_an_explicit_tolerance_override():
    """A caller CAN still pass a wider tolerance explicitly -- the fix is
    to the default, not to removing the parameter."""
    client = FakeSaltClient()
    body = {"message": {"id": "msg-1"}, "chat_id": "chat-1"}
    one_day_ago = int(time.time()) - (24 * 60 * 60)
    headers, raw = sign_body(body, timestamp=one_day_ago)
    headers["X-Salt-Agent-Id"] = "agent-self"
    row = {"id": 1, "headers": headers, "body": raw, "delivery_id": "d-1", "created_at": "a day ago"}
    client.update_rounds = [{"updates": [row], "cursor": 1}]

    cursor = sc.PersistentCursor(sc.state_dir("agent-self", "test-tolerance-override") / "cursor.json")
    event = sc.poll_for_event(
        client,
        "sk-test",
        secret=WEBHOOK_SECRET,
        cursor=cursor,
        wait_seconds=0,
        predicate=lambda e: True,
        tolerance_seconds=7 * 24 * 60 * 60,
    )

    assert event is not None
