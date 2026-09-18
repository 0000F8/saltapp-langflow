# Salt Send Message: real PGP round-trip (generate two real keypairs,
# encrypt through the component, decrypt with each recipient's own private
# key) plus the no-recipients error path.
from __future__ import annotations

import pytest
from saltapp import crypto
from saltapp.errors import SaltApiError

from saltapp_langflow import send_message
from saltapp_langflow.send_message import SaltSendMessageComponent

from .conftest import FakeSaltClient


def _make_component(monkeypatch, client: FakeSaltClient, **field_overrides) -> SaltSendMessageComponent:
    monkeypatch.setattr(send_message.sc, "get_client", lambda host: client)
    component = SaltSendMessageComponent()
    fields = {
        "host": "https://example.test",
        "agent_id": "agent-self",
        "api_key": "sk-test",
        "private_key": "",
        "passphrase": "",
        "chat_id": "chat-1",
        "text": "hello there",
        "quiet": False,
    }
    fields.update(field_overrides)
    component.set(**fields)
    return component


def test_send_message_encrypts_for_every_recipient_and_a_self_copy(monkeypatch):
    self_keys = crypto.generate_keypair("self-pass")
    other_keys = crypto.generate_keypair("other-pass")

    client = FakeSaltClient()
    client.chat_members_response = [
        {"id": "agent-self", "public_key": self_keys.public_key},
        {"id": "user-2", "public_key": other_keys.public_key},
    ]

    component = _make_component(monkeypatch, client)
    result = component.send()

    assert result.text == "Sent to chat chat-1."
    assert "message id msg-1" in component.status

    post_calls = [c for c in client.calls if c[0] == "post_message"]
    assert len(post_calls) == 1
    _, args, kwargs = post_calls[0]
    api_key, chat_id, ciphertext, sender_copy = args
    assert api_key == "sk-test"
    assert chat_id == "chat-1"

    # Real decrypt, not a stubbed check: the recipient's own private key
    # must recover exactly the plaintext that was sent.
    decrypted_for_recipient = crypto.decrypt(ciphertext, other_keys.private_key, "other-pass")
    assert decrypted_for_recipient == "hello there"

    # The sender copy exists and decrypts under the SENDER's own key too.
    assert sender_copy is not None
    decrypted_for_sender = crypto.decrypt(sender_copy, self_keys.private_key, "self-pass")
    assert decrypted_for_sender == "hello there"

    assert client.closed is True


def test_send_message_raises_when_nobody_else_has_a_public_key(monkeypatch):
    self_keys = crypto.generate_keypair("self-pass")
    client = FakeSaltClient()
    # Only this agent's own row has a key -- nobody else to encrypt for.
    client.chat_members_response = [{"id": "agent-self", "public_key": self_keys.public_key}]

    component = _make_component(monkeypatch, client)

    with pytest.raises(SaltApiError, match="no recipient public keys"):
        component.send()

    assert not any(c[0] == "post_message" for c in client.calls)
