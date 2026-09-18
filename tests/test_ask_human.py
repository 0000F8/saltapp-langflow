from __future__ import annotations

from saltapp_langflow import ask_human
from saltapp_langflow.ask_human import SaltAskHumanComponent

from .conftest import FakeSaltClient, signed_update_row


def _make_component(monkeypatch, client: FakeSaltClient, **field_overrides) -> SaltAskHumanComponent:
    monkeypatch.setattr(ask_human.sc, "get_client", lambda host: client)
    component = SaltAskHumanComponent()
    fields = {
        "host": "https://example.test",
        "agent_id": "agent-self",
        "api_key": "sk-test",
        "private_key": "",
        "passphrase": "",
        "chat_id": "chat-1",
        "question": "Which environment?",
        "options": "Yes,No,Maybe",
        "wait_seconds": 50,
    }
    fields.update(field_overrides)
    component.set(**fields)
    return component


def test_ask_human_returns_the_tapped_option_immediately(monkeypatch):
    client = FakeSaltClient()
    client.post_card_response = {"id": "card-1"}
    tap_body = {
        "card_id": "card-1",
        "action_id": "opt_1",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "ada", "account_type": "User"},
        "state": {"blocks": []},
    }
    client.update_rounds = [{"updates": [signed_update_row(1, tap_body)], "cursor": 1}]

    component = _make_component(monkeypatch, client)
    result = component.ask()

    assert result.text == "No"
    assert "ada" in component.status
    assert "'No'" in component.status

    post_card_calls = [c for c in client.calls if c[0] == "post_card"]
    assert len(post_card_calls) == 1
    _, args, _kwargs = post_card_calls[0]
    _api_key, chat_id, blocks, text = args
    assert chat_id == "chat-1"
    assert text == "Which environment?"
    # One section (the question) + one actions row (the three buttons).
    assert blocks[0]["type"] == "section"
    assert blocks[0]["text"] == "Which environment?"
    assert [b["label"] for b in blocks[1]["elements"]] == ["Yes", "No", "Maybe"]

    assert client.closed is True


def test_ask_human_times_out_with_no_taps(monkeypatch):
    client = FakeSaltClient()
    client.post_card_response = {"id": "card-1"}
    client.update_rounds = [{"updates": [], "cursor": 0}]

    component = _make_component(monkeypatch, client, wait_seconds=0)
    result = component.ask()

    assert result.text == "No answer within 0s."
    assert component.status == "No answer within 0s."


def test_ask_human_ignores_a_tap_from_another_agent(monkeypatch):
    client = FakeSaltClient()
    client.post_card_response = {"id": "card-1"}
    agent_tap_body = {
        "card_id": "card-1",
        "action_id": "opt_0",
        "chat_id": "chat-1",
        "user": {"id": "agent-3", "username": "some_other_agent", "account_type": "Agent"},
        "state": {"blocks": []},
    }
    client.update_rounds = [{"updates": [signed_update_row(1, agent_tap_body)], "cursor": 1}]

    component = _make_component(monkeypatch, client, wait_seconds=0)
    result = component.ask()

    # An agent's own tap must never resolve a pending human ask -- the
    # round still "happens" (post_card + one poll), but nothing answers.
    assert result.text == "No answer within 0s."


def test_ask_human_wait_seconds_is_capped_at_fifty(monkeypatch):
    """wait_seconds=999 must clamp to MAX_WAIT_SECONDS (50) -- faking the
    monotonic clock to jump far forward between poll rounds keeps this
    test fast regardless of the cap; the clamp itself is a static
    computation on the input, not something that needs 50 real seconds to
    observe."""
    client = FakeSaltClient()
    client.post_card_response = {"id": "card-1"}
    client.update_rounds = [{"updates": [], "cursor": 0}]

    clock = iter([0.0, 100000.0, 200000.0, 300000.0])
    monkeypatch.setattr(ask_human.sc.time, "monotonic", lambda: next(clock))

    component = _make_component(monkeypatch, client, wait_seconds=999)
    result = component.ask()

    assert result.text == "No answer within 50s."


def test_ask_human_with_no_options_does_not_call_the_api(monkeypatch):
    client = FakeSaltClient()
    component = _make_component(monkeypatch, client, options="")

    result = component.ask()

    assert result.text == "No options given; nothing to ask."
    assert client.calls == []
