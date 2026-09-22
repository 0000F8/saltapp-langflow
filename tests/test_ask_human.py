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
        "card_id": "",
    }
    fields.update(field_overrides)
    component.set(**fields)
    return component


def _get_agent_updates_calls(client: FakeSaltClient) -> list:
    return [c for c in client.calls if c[0] == "get_agent_updates"]


def test_ask_human_posts_a_card_and_returns_pending_when_nothing_tapped_yet(monkeypatch):
    client = FakeSaltClient()
    client.post_card_response = {"id": "card-1"}
    client.update_rounds = [{"updates": [], "cursor": 0}]

    component = _make_component(monkeypatch, client)
    result = component.ask()

    assert result.text == "pending:card-1"
    assert "card_id='card-1'" in component.status
    assert len(_get_agent_updates_calls(client)) == 1  # exactly one check, no loop

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


def test_ask_human_returns_the_answer_immediately_when_the_instant_tap_is_already_there(monkeypatch):
    """The one check_for_event call this fresh post makes still covers the
    rare instant-tap case."""
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
    assert len(_get_agent_updates_calls(client)) == 1


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

    component = _make_component(monkeypatch, client)
    result = component.ask()

    # An agent's own tap must never resolve a pending human ask -- the
    # one check still happens, but nothing answers.
    assert result.text == "pending:card-1"
    assert len(_get_agent_updates_calls(client)) == 1


def test_ask_human_rechecks_an_existing_card_id_without_posting_a_new_one(monkeypatch):
    client = FakeSaltClient()
    tap_body = {
        "card_id": "card-42",
        "action_id": "opt_0",
        "chat_id": "chat-1",
        "user": {"id": "user-2", "username": "ada", "account_type": "User"},
        "state": {"blocks": []},
    }
    client.update_rounds = [{"updates": [signed_update_row(1, tap_body)], "cursor": 1}]

    component = _make_component(monkeypatch, client, card_id="card-42")
    result = component.ask()

    assert result.text == "Yes"
    assert not any(c[0] == "post_card" for c in client.calls)  # no new card posted
    assert len(_get_agent_updates_calls(client)) == 1


def test_ask_human_recheck_returns_pending_again_with_no_tap(monkeypatch):
    client = FakeSaltClient()
    client.update_rounds = [{"updates": [], "cursor": 0}]

    component = _make_component(monkeypatch, client, card_id="card-42")
    result = component.ask()

    assert result.text == "pending:card-42"
    assert "Still" in component.status
    assert not any(c[0] == "post_card" for c in client.calls)
    assert len(_get_agent_updates_calls(client)) == 1


def test_ask_human_with_no_options_and_no_card_id_does_not_call_the_api(monkeypatch):
    client = FakeSaltClient()
    component = _make_component(monkeypatch, client, options="", card_id="")

    result = component.ask()

    assert result.text == "No options given; nothing to ask."
    assert client.calls == []
