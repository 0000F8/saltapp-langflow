# Salt Ask Human: post a card with button options into a Salt chat and
# block (up to MAX_WAIT_SECONDS) for a human to tap one. tool_mode is on
# for chat_id/question/options so an Agent component can call this
# directly. See AGENTS.md ("The ask/poll design") for the full write-up.
from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, MultilineSecretInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

from . import _salt_common as sc

# The task brief is explicit: wait "up to 50s" -- this is a hard cap, not a
# default, so a caller (human or an Agent component's LLM) cannot push it
# past what a Langflow run / any fronting proxy can comfortably hold open.
MAX_WAIT_SECONDS = 50


class SaltAskHumanComponent(Component):
    display_name = "Salt Ask Human"
    description = (
        "Post a question with button options into a Salt chat and wait, up to 50 seconds, "
        "for a human to tap one. Returns the tapped option, or a plain 'no answer' result on timeout."
    )
    icon = "help-circle"
    name = "SaltAskHuman"

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
            info="This agent's own Salt id, used only to namespace this component's local poll-cursor file.",
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
            info=(
                "Not needed here, even to read the answer: a tapped button arrives as plain "
                f"structured data, never PGP ciphertext. {sc.KEYLESS_INFO}"
            ),
        ),
        SecretStrInput(
            name="passphrase",
            display_name="Private Key Passphrase (optional)",
            required=False,
            info="Not needed here -- this component never decrypts anything.",
        ),
        MessageTextInput(
            name="chat_id",
            display_name="Chat ID",
            tool_mode=True,
            info="The Salt chat to ask in.",
        ),
        MessageTextInput(
            name="question",
            display_name="Question",
            tool_mode=True,
            info="The question shown on the card.",
        ),
        MessageTextInput(
            name="options",
            display_name="Options",
            value="Yes,No",
            tool_mode=True,
            info='Comma-separated button labels, 1 to 5, e.g. "Yes,No" or "staging,production".',
        ),
        IntInput(
            name="wait_seconds",
            display_name="Wait Seconds",
            value=MAX_WAIT_SECONDS,
            info=f"How long to wait for a tap before giving up. Capped at {MAX_WAIT_SECONDS}s.",
        ),
    ]

    outputs = [Output(display_name="Answer", name="answer", method="ask")]

    def ask(self) -> Message:
        options = sc.parse_options(self.options)
        if not options:
            self.status = "No options given; nothing to ask."
            return Message(text=self.status)

        wait_seconds = min(max(0, int(self.wait_seconds or 0)), MAX_WAIT_SECONDS)

        client = sc.get_client(self.host)
        try:
            identity = sc.resolve_identity(client, self.api_key)
            secret = identity.get("webhook_secret")
            agent_id = self.agent_id or identity.get("agent_id") or ""
            if not secret:
                self.status = "This agent has no webhook secret yet; cannot verify a tap."
                return Message(text=self.status)

            blocks = sc.build_question_card(self.question, options)
            card = client.post_card(self.api_key, self.chat_id, blocks, self.question)
            card_id = str(card.get("card_id") or card.get("id") or "")
            if not card_id:
                self.status = "Salt did not return a card id; cannot match the answer."
                return Message(text=self.status)

            label_by_action = {f"opt_{i}": label for i, label in enumerate(options)}

            def is_answer(event: Any) -> bool:
                if event.type != "card_interaction":
                    return False
                body = event.body or {}
                if str(body.get("card_id") or "") != card_id:
                    return False
                tapper = body.get("user") or {}
                # A tap from another agent never resolves a pending HUMAN ask.
                return str(tapper.get("account_type") or "").lower() != "agent"

            cursor = sc.PersistentCursor(sc.state_dir(agent_id, "ask_human") / "cursor.json")
            event = sc.poll_for_event(
                client,
                self.api_key,
                secret=secret,
                cursor=cursor,
                wait_seconds=wait_seconds,
                predicate=is_answer,
            )
        finally:
            client.close()

        if event is None:
            self.status = f"No answer within {wait_seconds}s."
            return Message(text=self.status)

        body = event.body or {}
        action_id = str(body.get("action_id") or "")
        answer_label = label_by_action.get(action_id, action_id)
        tapper = body.get("user") or {}
        answered_by = tapper.get("username") or tapper.get("id") or "someone"
        self.status = f"{answered_by} tapped {answer_label!r}."
        return Message(text=answer_label)
