# Salt Ask Human: post a card with button options into a Salt chat, then
# check ONCE for a tap and always return -- either the answer, or a
# pending result carrying the card's id so a LATER flow run (this same
# component, re-invoked with `card_id` set) can check again. There is no
# waiting here: a Langflow component's build method is a single stateless
# call with no persistent process behind it, and per the owner's explicit
# rule this package must never loop or sleep waiting for something to
# happen. See AGENTS.md ("The ask/check design") for the full write-up,
# and README.md's "Push vs. on-demand" section for what changes if you
# need a genuine wait-then-answer instead of this check-yourself shape.
from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, MultilineSecretInput, Output, SecretStrInput, StrInput
from lfx.schema.message import Message

from . import _salt_common as sc


class SaltAskHumanComponent(Component):
    display_name = "Salt Ask Human"
    description = (
        "Post a question with button options into a Salt chat and check once for a tap. "
        "Returns the tapped option if one is already there, or a pending result to check again later."
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
            info="This agent's own Salt id, used only to namespace this component's local check-cursor file.",
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
            required=False,
            info="The question shown on the card. Only used when card_id is blank (posting a new question).",
        ),
        MessageTextInput(
            name="options",
            display_name="Options",
            value="Yes,No",
            tool_mode=True,
            info='Comma-separated button labels, 1 to 5, e.g. "Yes,No" or "staging,production". '
            "Required when card_id is blank; not needed to re-check an existing card.",
        ),
        MessageTextInput(
            name="card_id",
            display_name="Card ID (to re-check)",
            required=False,
            tool_mode=True,
            info=(
                "Leave blank to post a new question. Fill in a card_id from a previous pending result of "
                "this same component to check whether it's been answered yet, without posting a new card."
            ),
        ),
    ]

    outputs = [Output(display_name="Answer", name="answer", method="ask")]

    def ask(self) -> Message:
        card_id = str(self.card_id or "").strip()
        is_recheck = bool(card_id)
        options = sc.parse_options(self.options)

        if not is_recheck and not options:
            self.status = "No options given; nothing to ask."
            return Message(text=self.status)

        client = sc.get_client(self.host)
        try:
            identity = sc.resolve_identity(client, self.api_key)
            secret = identity.get("webhook_secret")
            agent_id = self.agent_id or identity.get("agent_id") or ""
            if not secret:
                self.status = "This agent has no webhook secret yet; cannot verify a tap."
                return Message(text=self.status)

            if not is_recheck:
                blocks = sc.build_question_card(self.question, options)
                card = client.post_card(self.api_key, self.chat_id, blocks, self.question)
                card_id = str(card.get("card_id") or card.get("id") or "")
                if not card_id:
                    self.status = "Salt did not return a card id; cannot match the answer."
                    return Message(text=self.status)

            # Only meaningful if `options` was supplied this call (always
            # true on a fresh post; on a re-check it is whatever the
            # caller happened to pass again, or blank -- see the
            # `options` field's own info text). A recheck that cannot map
            # `action_id` back to a label still returns the raw
            # `action_id`, never fails.
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

            # Shared with Salt Read Updates' own cursor -- see
            # SHARED_POLL_PURPOSE's docstring: there is only one ack per
            # agent server-side, so this package's local bookkeeping
            # matches that instead of pretending each component has its
            # own.
            cursor = sc.PersistentCursor(sc.state_dir(agent_id, sc.SHARED_POLL_PURPOSE) / "cursor.json")
            matches, _new_cursor = sc.check_for_event(
                client,
                self.api_key,
                secret=secret,
                cursor=cursor,
                predicate=is_answer,
            )
        finally:
            client.close()

        event = matches[0] if matches else None

        if event is None:
            prefix = "Posted" if not is_recheck else "Still"
            self.status = (
                f"{prefix}; no tap yet. Re-run this component with card_id={card_id!r} to check again, "
                "or see README for wiring up push via a webhook receiver."
            )
            return Message(text=f"pending:{card_id}")

        body = event.body or {}
        action_id = str(body.get("action_id") or "")
        answer_label = label_by_action.get(action_id, action_id)
        tapper = body.get("user") or {}
        answered_by = tapper.get("username") or tapper.get("id") or "someone"
        self.status = f"{answered_by} tapped {answer_label!r}."
        return Message(text=answer_label)
