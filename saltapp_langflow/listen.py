# Salt Trigger/Listen: poll this agent's Salt updates for the next new,
# verified event since the last time this component ran, instead of
# Langflow's built-in Webhook component -- see AGENTS.md and README.md for
# why (short version: Webhook exposes only the request BODY, never
# headers, and Salt's signature lives in a header; an unverified
# card_interaction/invoice_paid/chat_opened payload is plaintext and
# actionable, so it must never be trusted unverified).
from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import IntInput, MessageTextInput, MultilineSecretInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

from . import _salt_common as sc

# A "listen" component is meant to sit at the start of a flow that is
# itself triggered on its own schedule/cron/manual run and pick up
# whatever is new since last time -- not to hold a request open for
# minutes. Capped well under a minute so it plays nicely with whatever
# fronts this Langflow run (a browser tab, a reverse proxy, a scheduler).
MAX_WAIT_SECONDS = 55
DEFAULT_WAIT_SECONDS = 25

KNOWN_EVENT_TYPES = {
    "message",
    "card_interaction",
    "invoice_paid",
    "chat_opened",
    "handoff_confirmed",
    "handoff_received",
}


class SaltListenComponent(Component):
    display_name = "Salt Trigger/Listen"
    description = (
        "Poll this agent's Salt updates for the next new event (message, card tap, payment, "
        "chat opened, or hand-off) since the last run, verifying its signature first."
    )
    icon = "radio"
    name = "SaltListen"

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
            info="This agent's own Salt id, used to namespace this component's local poll-cursor file.",
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
                "Not needed here: every event this returns is metadata or structured data, "
                f"never PGP ciphertext. {sc.KEYLESS_INFO}"
            ),
        ),
        SecretStrInput(
            name="passphrase",
            display_name="Private Key Passphrase (optional)",
            required=False,
            info="Not needed here -- this component never decrypts anything.",
        ),
        IntInput(
            name="wait_seconds",
            display_name="Wait Seconds",
            value=DEFAULT_WAIT_SECONDS,
            info=f"How long to keep polling for a new event before returning empty. Capped at {MAX_WAIT_SECONDS}s.",
        ),
        MessageTextInput(
            name="event_types",
            display_name="Event Types",
            required=False,
            info=(
                "Comma-separated event types to wait for: message, card_interaction, invoice_paid, "
                "chat_opened, handoff_confirmed, handoff_received. Blank means any of them."
            ),
        ),
    ]

    outputs = [Output(display_name="Event", name="event", method="listen")]

    def listen(self) -> Data:
        wanted = {t for t in sc.parse_options(self.event_types) if t in KNOWN_EVENT_TYPES}
        wait_seconds = min(max(0, int(self.wait_seconds or 0)), MAX_WAIT_SECONDS)

        client = sc.get_client(self.host)
        try:
            identity = sc.resolve_identity(client, self.api_key)
            secret = identity.get("webhook_secret")
            agent_id = self.agent_id or identity.get("agent_id") or ""
            if not secret:
                self.status = "This agent has no webhook secret yet; cannot verify events."
                return Data(data={"status": "no_secret"})

            def matches(event: Any) -> bool:
                if event.type == "unknown":
                    return False
                return not wanted or event.type in wanted

            cursor = sc.PersistentCursor(sc.state_dir(agent_id, "listen") / "cursor.json")
            event = sc.poll_for_event(
                client,
                self.api_key,
                secret=secret,
                cursor=cursor,
                wait_seconds=wait_seconds,
                predicate=matches,
            )
        finally:
            client.close()

        if event is None:
            self.status = f"No new events within {wait_seconds}s."
            return Data(data={"status": "no_new_events"})

        self.status = f"New {event.type} event (delivery {event.delivery_id})."
        return Data(
            data={
                "status": "event",
                "event_type": event.type,
                "delivery_id": event.delivery_id,
                "created_at": event.created_at,
                "body": event.body,
            }
        )
