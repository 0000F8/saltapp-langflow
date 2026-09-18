from __future__ import annotations

from saltapp_langflow import listen
from saltapp_langflow.listen import SaltListenComponent

from .conftest import FakeSaltClient, signed_update_row


def _make_component(monkeypatch, client: FakeSaltClient, **field_overrides) -> SaltListenComponent:
    monkeypatch.setattr(listen.sc, "get_client", lambda host: client)
    component = SaltListenComponent()
    fields = {
        "host": "https://example.test",
        "agent_id": "agent-self",
        "api_key": "sk-test",
        "private_key": "",
        "passphrase": "",
        "wait_seconds": 0,
        "event_types": "",
    }
    fields.update(field_overrides)
    component.set(**fields)
    return component


def test_listen_returns_no_new_events_on_an_empty_poll(monkeypatch):
    client = FakeSaltClient()
    client.update_rounds = [{"updates": [], "cursor": 0}]

    component = _make_component(monkeypatch, client)
    result = component.listen()

    assert result.data == {"status": "no_new_events"}
    assert "No new events within 0s." == component.status
    assert client.closed is True


def test_listen_filters_by_event_type_within_one_round(monkeypatch):
    client = FakeSaltClient()
    message_body = {"message": {"id": "msg-1"}, "chat_id": "chat-1"}
    invoice_body = {"type": "invoice_paid", "chat_id": "chat-1", "transfer_request_id": "tr-1"}
    client.update_rounds = [
        {
            "updates": [
                signed_update_row(1, message_body),
                signed_update_row(2, invoice_body),
            ],
            "cursor": 2,
        }
    ]

    component = _make_component(monkeypatch, client, event_types="invoice_paid")
    result = component.listen()

    assert result.data["status"] == "event"
    assert result.data["event_type"] == "invoice_paid"
    assert result.data["body"] == invoice_body


def test_listen_wait_seconds_is_capped(monkeypatch):
    """wait_seconds=9999 must clamp to MAX_WAIT_SECONDS (55) -- faking the
    monotonic clock to jump far forward between poll rounds keeps this
    test fast regardless of the cap, since the clamp itself is a static
    computation on the input, not something that needs 55 real seconds to
    observe."""
    client = FakeSaltClient()
    client.update_rounds = [{"updates": [], "cursor": 0}]

    clock = iter([0.0, 100000.0, 200000.0, 300000.0])
    monkeypatch.setattr(listen.sc.time, "monotonic", lambda: next(clock))

    component = _make_component(monkeypatch, client, wait_seconds=9999)
    result = component.listen()

    assert result.data == {"status": "no_new_events"}
    assert "No new events within 55s." == component.status


def test_listen_persists_its_cursor_across_component_runs(monkeypatch):
    """A second 'flow run' (a fresh component + a fresh fake client) must
    start polling from where the first run's cursor left off, not from 0 --
    the whole point of PersistentCursor."""
    first_client = FakeSaltClient()
    first_body = {"message": {"id": "msg-1"}, "chat_id": "chat-1"}
    first_client.update_rounds = [{"updates": [signed_update_row(5, first_body)], "cursor": 5}]

    first_run = _make_component(monkeypatch, first_client)
    first_result = first_run.listen()
    assert first_result.data["status"] == "event"

    second_client = FakeSaltClient()
    second_client.update_rounds = [{"updates": [], "cursor": 5}]

    second_run = _make_component(monkeypatch, second_client)
    second_run.listen()

    after_values = [kwargs["after"] for name, _args, kwargs in second_client.calls if name == "get_agent_updates"]
    assert after_values == [5]
