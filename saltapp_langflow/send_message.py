# Salt Send Message: post an end-to-end encrypted plaintext message into a
# Salt chat. See _salt_common.send_encrypted_message for the actual logic
# (fetch chat members, encrypt for everyone but this agent, post).
from __future__ import annotations

from lfx.custom.custom_component.component import Component
from lfx.io import BoolInput, MessageTextInput, MultilineSecretInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

from . import _salt_common as sc


class SaltSendMessageComponent(Component):
    display_name = "Salt Send Message"
    description = "Send an end-to-end encrypted message into a Salt chat."
    icon = "send"
    name = "SaltSendMessage"

    inputs = [
        StrInput(
            name="host",
            display_name="Salt Host",
            value=sc.SALT_HOST_DEFAULT,
            info="The Salt deployment this agent is registered on.",
        ),
        StrInput(
            name="agent_id",
            display_name="Agent ID",
            info="This agent's own Salt id, used to find its own row in the chat's member list.",
        ),
        SecretStrInput(
            name="api_key",
            display_name="API Key",
            info="This agent's Salt API key.",
        ),
        MultilineSecretInput(
            name="private_key",
            display_name="Agent Private Key (optional)",
            required=False,
            info=f"Not needed to send a message. {sc.KEYLESS_INFO}",
        ),
        SecretStrInput(
            name="passphrase",
            display_name="Private Key Passphrase (optional)",
            required=False,
            info="Not needed to send a message -- this component never decrypts anything.",
        ),
        MessageTextInput(
            name="chat_id",
            display_name="Chat ID",
            tool_mode=True,
            info="The Salt chat to send into.",
        ),
        MessageTextInput(
            name="text",
            display_name="Message",
            tool_mode=True,
            info="The plain text to send. It is encrypted for every current chat member before it leaves this process.",
        ),
        BoolInput(
            name="quiet",
            display_name="Quiet",
            value=False,
            advanced=True,
            info="Skip push notifications for this one message.",
        ),
    ]

    outputs = [Output(display_name="Result", name="result", method="send")]

    def send(self) -> Message:
        client = sc.get_client(self.host)
        try:
            result = sc.send_encrypted_message(
                client,
                api_key=self.api_key,
                agent_id=self.agent_id,
                chat_id=self.chat_id,
                text=self.text,
                quiet=bool(self.quiet),
            )
        finally:
            client.close()

        message_id = result.get("id") or (result.get("message") or {}).get("id")
        self.status = f"Sent to chat {self.chat_id} (message id {message_id})."
        return Message(text=f"Sent to chat {self.chat_id}.")
