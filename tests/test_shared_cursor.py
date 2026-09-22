"""Fixed 2026-09-22: Salt Ask Human and Salt Read Updates share one check
cursor file (sc.SHARED_POLL_PURPOSE), matching salt-api's real one-ack-per-
agent model -- see AGENTS.md's "File-based cursor state" for the full
reasoning. These tests prove (1) the two components' state_dir calls now
resolve to the same file, and (2) check_for_event's cursor genuinely
advances so a second call after processing does not redeliver the same
rows (team-lead's explicit ask after the production ack-semantics
finding).
"""
from __future__ import annotations

from saltapp_langflow import _salt_common as sc

from .conftest import FakeSaltClient, WEBHOOK_SECRET, signed_update_row


def test_ask_human_and_read_updates_resolve_to_the_same_cursor_file():
    ask_human_dir = sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE)
    read_updates_dir = sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE)
    assert ask_human_dir == read_updates_dir
    assert (ask_human_dir / "cursor.json") == (read_updates_dir / "cursor.json")


def test_a_check_by_one_component_is_visible_to_the_other_via_the_shared_cursor():
    cursor_path = sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE) / "cursor.json"
    first = sc.PersistentCursor(cursor_path)
    first.set(42)

    # A second PersistentCursor instance pointed at the SAME shared purpose
    # (as ask_human.py and read_updates.py both construct) reads it back --
    # this is what makes "whichever component checked most recently" the
    # single source of truth locally too, matching the server's own single
    # ack per agent.
    second = sc.PersistentCursor(sc.state_dir("agent-self", sc.SHARED_POLL_PURPOSE) / "cursor.json")
    assert second.get() == 42


def test_check_for_event_advances_the_cursor_so_a_second_check_does_not_redeliver_processed_rows(tmp_path):
    """Direct evidence for the production finding: the ack (and this
    package's local mirror of it) must ADVANCE on every check, or a client
    keeps re-fetching the same rows forever. First check processes row 1;
    the cursor this check leaves behind is what the SECOND check must send
    to actually see nothing new."""
    client = FakeSaltClient()
    body = {"message": {"id": "msg-1"}, "chat_id": "chat-1"}
    client.update_rounds = [
        {"updates": [signed_update_row(1, body, agent_id="agent-self")], "cursor": 1},
    ]

    cursor = sc.PersistentCursor(tmp_path / "cursor.json")
    first_matches, first_cursor = sc.check_for_event(
        client, "sk-test", secret=WEBHOOK_SECRET, cursor=cursor, predicate=lambda e: True,
    )
    assert len(first_matches) == 1
    assert first_cursor == 1
    assert cursor.get() == 1  # advanced past the row this check actually saw

    # A second, independent check call (a later component run) using the
    # SAME persisted cursor must send the advanced position, and the
    # (fake) server -- correctly modeling "rows with id <= after are never
    # returned again" -- then has nothing left to redeliver.
    second_client = FakeSaltClient()
    second_client.update_rounds = [{"updates": [], "cursor": 1}]
    second_matches, second_cursor = sc.check_for_event(
        second_client, "sk-test", secret=WEBHOOK_SECRET, cursor=cursor, predicate=lambda e: True,
    )

    assert second_matches == []
    assert second_cursor == 1
    after_values = [kwargs["after"] for name, _args, kwargs in second_client.calls if name == "get_agent_updates"]
    assert after_values == [1]  # sent the advanced cursor, not 0
