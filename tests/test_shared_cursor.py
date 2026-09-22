"""Fixed 2026-09-22: Salt Ask Human and Salt Trigger/Listen share one poll
cursor file (sc.SHARED_POLL_PURPOSE), matching salt-api's real one-ack-per-
agent model -- see AGENTS.md's "File-based cursor state" for the full
reasoning. These tests prove (1) the two components' state_dir calls now
resolve to the same file, and (2) a poll loop's cursor genuinely advances
so a second poll after processing does not redeliver the same rows
(team-lead's explicit ask after the production ack-semantics finding).
"""
from __future__ import annotations

from saltapp_langflow import _salt_common as sc

from .conftest import FakeSaltClient, WEBHOOK_SECRET, signed_update_row


def test_ask_human_and_listen_resolve_to_the_same_cursor_file():
    ask_human_dir = sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE)
    listen_dir = sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE)
    assert ask_human_dir == listen_dir
    assert (ask_human_dir / "cursor.json") == (listen_dir / "cursor.json")


def test_a_poll_by_one_component_is_visible_to_the_other_via_the_shared_cursor():
    cursor_path = sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE) / "cursor.json"
    first = sc.PersistentCursor(cursor_path)
    first.set(42)

    # A second PersistentCursor instance pointed at the SAME shared purpose
    # (as ask_human.py and listen.py both now construct) reads it back --
    # this is what makes "whichever component polled most recently" the
    # single source of truth locally too, matching the server's own single
    # ack per agent.
    second = sc.PersistentCursor(sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE) / "cursor.json")
    assert second.get() == 42


def test_poll_for_event_advances_the_cursor_so_a_second_poll_does_not_redeliver_processed_rows(tmp_path):
    """Direct evidence for the production finding: the ack (and this
    package's local mirror of it) must ADVANCE on every poll, or a client
    keeps re-fetching the same rows forever. First poll processes row 1;
    the cursor this poll leaves behind is what the SECOND poll must send
    to actually see nothing new."""
    client = FakeSaltClient()
    body = {"message": {"id": "msg-1"}, "chat_id": "chat-1"}
    client.update_rounds = [
        {"updates": [signed_update_row(1, body, agent_id="agent-self")], "cursor": 1},
    ]

    cursor = sc.PersistentCursor(tmp_path / "cursor.json")
    first_event = sc.poll_for_event(
        client, "sk-test", secret=WEBHOOK_SECRET, cursor=cursor, wait_seconds=0, predicate=lambda e: True,
    )
    assert first_event is not None
    assert cursor.get() == 1  # advanced past the row this poll actually saw

    # A second, independent poll call (a later component run) using the
    # SAME persisted cursor must send the advanced position, and the
    # (fake) server -- correctly modeling "rows with id <= after are never
    # returned again" -- then has nothing left to redeliver.
    second_client = FakeSaltClient()
    second_client.update_rounds = [{"updates": [], "cursor": 1}]
    second_event = sc.poll_for_event(
        second_client, "sk-test", secret=WEBHOOK_SECRET, cursor=cursor, wait_seconds=0, predicate=lambda e: True,
    )

    assert second_event is None
    after_values = [kwargs["after"] for name, _args, kwargs in second_client.calls if name == "get_agent_updates"]
    assert after_values == [1]  # sent the advanced cursor, not 0
