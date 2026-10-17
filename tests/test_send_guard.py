# Send guard: the post carries recipient_fingerprints/sender_fingerprint, one retry on a
# stale_recipient_keys refusal re-encrypting to the returned keys, no loop, no guard when a
# recipient has no fingerprint. Real PGP; only the HTTP client is faked.
from __future__ import annotations

import pytest
from saltapp import crypto
from saltapp.errors import SaltApiError

from saltapp_langflow import _salt_common as sc

from .conftest import FakeSaltClient

STALE = {"code": "stale_recipient_keys"}


def _setup(prints=True):
    self_keys = crypto.generate_keypair("s")
    other_keys = crypto.generate_keypair("o")
    other = {"id": "user-2", "public_key": other_keys.public_key}
    if prints:
        other["public_fingerprint"] = crypto.fingerprint_of(other_keys.public_key)
    members = [{"id": "agent-self", "public_key": self_keys.public_key}, other]
    return self_keys, other_keys, members


def _post_calls(client):
    return [c for c in client.calls if c[0] == "post_message"]


def _refuse(client, errors):
    real = client.post_message

    def post(*args, **kwargs):
        if errors:
            client._record("post_message", *args, **kwargs)
            raise errors.pop(0)
        return real(*args, **kwargs)

    client.post_message = post


def test_guard_fields_are_sent():
    self_keys, other_keys, members = _setup()
    client = FakeSaltClient()
    sc.send_encrypted_message(client, api_key="k", agent_id="agent-self", chat_id="c", text="hi", members=members)
    _, _, kwargs = _post_calls(client)[0]
    assert kwargs["recipient_fingerprints"] == {"user-2": crypto.fingerprint_of(other_keys.public_key).upper()}
    assert kwargs["sender_fingerprint"] == crypto.fingerprint_of(self_keys.public_key).upper()


def test_no_guard_when_a_fingerprint_is_missing():
    _, _, members = _setup(prints=False)
    client = FakeSaltClient()
    sc.send_encrypted_message(client, api_key="k", agent_id="agent-self", chat_id="c", text="hi", members=members)
    _, _, kwargs = _post_calls(client)[0]
    assert kwargs["recipient_fingerprints"] is None
    assert kwargs["sender_fingerprint"] is None


def test_one_retry_on_stale_recipient_keys_encrypts_to_the_returned_keys():
    _, _, members = _setup()
    new_keys = crypto.generate_keypair("n")
    fresh = [{"id": "user-2", "public_key": new_keys.public_key,
              "public_fingerprint": crypto.fingerprint_of(new_keys.public_key), "key_generation": 2}]
    client = FakeSaltClient()
    _refuse(client, [SaltApiError("POST", "u", 409, {**STALE, "members": fresh})])
    sc.send_encrypted_message(client, api_key="k", agent_id="agent-self", chat_id="c", text="hi", members=members)
    calls = _post_calls(client)
    assert len(calls) == 2
    _, args, kwargs = calls[1]
    assert crypto.decrypt(args[2], new_keys.private_key, "n") == "hi"
    assert kwargs["recipient_fingerprints"] == {"user-2": crypto.fingerprint_of(new_keys.public_key).upper()}


def test_second_refusal_propagates_without_looping():
    _, _, members = _setup()
    new_keys = crypto.generate_keypair("n")
    body = {**STALE, "members": [{"id": "user-2", "public_key": new_keys.public_key,
                                  "public_fingerprint": crypto.fingerprint_of(new_keys.public_key)}]}
    client = FakeSaltClient()
    _refuse(client, [SaltApiError("POST", "u", 409, body), SaltApiError("POST", "u", 409, body)])
    with pytest.raises(SaltApiError):
        sc.send_encrypted_message(client, api_key="k", agent_id="agent-self", chat_id="c", text="hi", members=members)
    assert len(_post_calls(client)) == 2


def test_other_errors_are_not_retried():
    _, _, members = _setup()
    client = FakeSaltClient()
    _refuse(client, [SaltApiError("POST", "u", 500, {"error": "boom"})])
    with pytest.raises(SaltApiError):
        sc.send_encrypted_message(client, api_key="k", agent_id="agent-self", chat_id="c", text="hi", members=members)
    assert len(_post_calls(client)) == 1
