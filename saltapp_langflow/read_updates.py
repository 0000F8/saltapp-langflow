# Salt Read Updates (was Salt Trigger/Listen): read this agent's Salt
# updates for whatever is new since the last check, in exactly ONE call,
# instead of Langflow's built-in Webhook component. See AGENTS.md and
# README.md for why not the built-in Webhook (short version: Webhook
# exposes only the request BODY, never headers, and Salt's signature
# lives in a header; an unverified card_interaction/invoice_paid/
# chat_opened payload is plaintext and actionable, so it must never be
# trusted unverified).
#
# On demand, not poll-and-wait: this component's build method runs once
# per flow invocation and returns immediately with whatever one
# GET /api/v1/agent/updates call finds -- no loop, no sleep, no wait
# budget. It is meant to sit at the start of a flow that is itself
# triggered on its own schedule/cron/manual run and pick up whatever is
# new since last time. Genuine push needs a persistent process outside
# this package; see README.md's "Push vs. on-demand" section.
from __future__ import annotations

from typing import Any

from lfx.custom.custom_component.component import Component
from lfx.io import MessageTextInput, MultilineSecretInput, Output, SecretStrInput, StrInput
from lfx.schema.data import Data

from . import _salt_common as sc

KNOWN_EVENT_TYPES = {
    "message",
    "card_interaction",
    "invoice_paid",
    "chat_opened",
    "handoff_confirmed",
    "handoff_received",
}


class SaltReadUpdatesComponent(Component):
    display_name = "Salt Read Updates"
    description = (
        "Check once for this agent's new Salt events (message, card tap, payment, chat opened, or "
        "hand-off) since the last check, verifying each one's signature first."
    )
    icon = "radio"
    name = "SaltReadUpdates"

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
            info="This agent's own Salt id, used to namespace this component's local check-cursor file.",
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
        MessageTextInput(
            name="event_types",
            display_name="Event Types",
            required=False,
            info=(
                "Comma-separated event types to look for: message, card_interaction, invoice_paid, "
                "chat_opened, handoff_confirmed, handoff_received. Blank means any of them."
            ),
        ),
    ]

    outputs = [Output(display_name="Events", name="events", method="read")]

    def read(self) -> Data:
        wanted = {t for t in sc.parse_options(self.event_types) if t in KNOWN_EVENT_TYPES}

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

            # Shared with Salt Ask Human's own cursor -- see
            # sc.SHARED_POLL_PURPOSE's docstring: there is only one ack
            # per agent server-side, so this package's local bookkeeping
            # matches that instead of pretending each component has its
            # own.
            cursor = sc.PersistentCursor(sc.state_dir(agent_id, sc.SHARED_POLL_PURPOSE) / "cursor.json")
            events, _new_cursor = sc.check_for_event(
                client,
                self.api_key,
                secret=secret,
                cursor=cursor,
                predicate=matches,
            )
        finally:
            client.close()

        if not events:
            self.status = "No new events since last check."
            return Data(data={"status": "no_new_events", "events": []})

        self.status = f"{len(events)} new event(s)."
        return Data(
            data={
                "status": "events",
                "events": [
                    {
                        "event_type": event.type,
                        "delivery_id": event.delivery_id,
                        "created_at": event.created_at,
                        "body": event.body,
                    }
                    for event in events
                ],
            }
        )
