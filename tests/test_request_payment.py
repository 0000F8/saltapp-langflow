from __future__ import annotations

from saltapp_langflow import request_payment
from saltapp_langflow.request_payment import SaltRequestPaymentComponent

from .conftest import FakeSaltClient


def test_request_payment_calls_the_api_with_the_right_shape(monkeypatch):
    client = FakeSaltClient()
    client.request_payment_response = {"id": "req-42", "status": "pending"}
    monkeypatch.setattr(request_payment.sc, "get_client", lambda host: client)

    component = SaltRequestPaymentComponent()
    component.set(
        host="https://example.test",
        agent_id="agent-self",
        api_key="sk-test",
        private_key="",
        passphrase="",
        chat_id="chat-1",
        receiver_id="user-2",
        wallet_id="wallet-9",
        amount="4.50",
        message="Coffee?",
    )

    result = component.request()

    assert result.data == {"id": "req-42", "status": "pending"}
    assert "4.50" in component.status
    assert "user-2" in component.status

    calls = [c for c in client.calls if c[0] == "request_payment"]
    assert len(calls) == 1
    _, args, kwargs = calls[0]
    assert args == ("sk-test",)
    assert kwargs == {
        "chat_id": "chat-1",
        "receiver_id": "user-2",
        "wallet_id": "wallet-9",
        "amount": "4.50",
        "message": "Coffee?",
    }
    assert client.closed is True


def test_request_payment_amount_is_always_a_string(monkeypatch):
    """amount must cross the wire as a human-decimal STRING, never a float
    (SaltClient.request_payment's own contract) -- a numeric-looking field
    value must still be stringified before it reaches the API."""
    client = FakeSaltClient()
    monkeypatch.setattr(request_payment.sc, "get_client", lambda host: client)

    component = SaltRequestPaymentComponent()
    component.set(
        host="https://example.test",
        agent_id="agent-self",
        api_key="sk-test",
        private_key="",
        passphrase="",
        chat_id="chat-1",
        receiver_id="user-2",
        wallet_id="wallet-9",
        amount="9",
        message="",
    )

    component.request()

    _, _args, kwargs = next(c for c in client.calls if c[0] == "request_payment")
    assert kwargs["amount"] == "9"
    assert isinstance(kwargs["amount"], str)
    assert kwargs["message"] is None
